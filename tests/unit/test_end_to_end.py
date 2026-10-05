"""The same three logical packages across native adapters, CSV and monitoring."""
import contextlib
import copy
import csv
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml
from support import CASES, FixtureRunner, ROOT, config_for, exporter, hold, report, resolve
from test_exporter import IDENTITY


class EndToEndTests(unittest.TestCase):
    inventory = [
        {'id': 'a', 'names': {'redos': 'held-rpm', 'astra': 'held-deb', 'alt': 'held-alt'}},
        {'id': 'b', 'names': {'redos': 'free-rpm', 'astra': 'free-deb', 'alt': 'free-alt'}},
        {'id': 'c', 'names': {'redos': 'absent-rpm', 'astra': 'absent-deb', 'alt': 'absent-alt'}},
    ]

    def inputs_for(self, system, transient=False):
        inputs = copy.deepcopy(next(case['inputs'] for case in CASES if case['case'] == system + '_held'))
        inputs['os_id'] = 'altlinux' if system == 'alt' else system
        packages = resolve(self.inventory, system)
        a, b = packages[0]['name'], packages[1]['name']
        if system == 'astra':
            want = 'hold' if transient else 'install'
            installed = a + '\tamd64\thold ok installed\n' + b + '\tamd64\t' + want + ' ok installed\n'
        else:
            installed = ''.join(name + '\t0\t1\t1\tx86_64\n' for name in (a, b))
            held_names = (a, b) if transient else (a,)
            inputs['locklist'] = ''.join(name + '-0:1-1.*\n' for name in held_names)
            inputs['apt_config_dump'] = {'rc': 0, 'stderr': '', 'stdout': ''.join(
                'RPM::Hold:: "^' + name + '$";\n' for name in held_names)}
        inputs['installed_query'] = {'rc': 0, 'stderr': '', 'stdout': installed}
        return inputs

    def test_installed_only_csv_metrics_lld_and_persistent_evidence_all_os(self):
        for system in ('redos', 'astra', 'alt'):
            with self.subTest(system=system), tempfile.TemporaryDirectory(dir=ROOT / '.build') as directory:
                packages = resolve(self.inventory, system)
                config = config_for(system, packages, str(Path(directory) / 'status.json'))
                inputs = self.inputs_for(system)
                runner = FixtureRunner(inputs, 'module')
                result = hold.execute(packages, config['backend'], adapter=hold.Backend(config['backend'], runner, runner.read))
                self.assertFalse(result['failed'], result)
                result['run_id'] = 'test'
                rows, coverage = report.aggregate('test', 'hold', {'host': {'result': result, 'os': {'id': system}}})
                content = report.csv_document(report.FIELDS['hold'], rows).decode('utf-8')
                csv_rows = list(csv.DictReader(io.StringIO(content)))
                self.assertEqual([row['package_id'] for row in csv_rows], ['a', 'b'])
                self.assertTrue(coverage[0]['success'])
                for at in (1000, 1300, 1600):
                    commands = FixtureRunner(inputs, 'exporter')
                    rc = exporter.scan(config, commands, commands.read, lambda: at, IDENTITY, str(Path(directory) / 'lock'))
                    self.assertEqual(rc, 0)
                cache = exporter.read_cache(config, IDENTITY)
                self.assertEqual([row['state'] for row in result['packages']], [row['state'] for row in cache['packages']])
                metrics = exporter.metrics(config, cache, 1601)
                self.assertNotIn('package_id="c"', metrics)
                self.assertIn('package_hold_status{package_id="a",package="' + packages[0]['name'] + '",method="' + config['backend'] + '"} 1', metrics)
                self.assertIn('package_hold_status{package_id="b",package="' + packages[1]['name'] + '",method="' + config['backend'] + '"} 0', metrics)
                snapshot = exporter.snapshot(config, cache, 1601)
                self.assertEqual([row['id'] for row in snapshot['packages'] if row['persistent']], ['b'])
                config_path = Path(directory) / 'config.json'
                config_path.write_text(json.dumps(config), encoding='utf-8')
                output = io.StringIO()
                with patch.object(exporter, 'boot_id', return_value=IDENTITY), patch.object(exporter.time, 'time', return_value=1601), patch.object(exporter.Commands, 'query', side_effect=AssertionError('Zabbix initiated a scan')), contextlib.redirect_stdout(output):
                    self.assertEqual(exporter.main(['zabbix-discovery', '--config', str(config_path)]), 0)
                discovery = json.loads(output.getvalue())
                self.assertEqual([row['{#PACKAGE_ID}'] for row in discovery], ['a', 'b'])

    @unittest.skipUnless(shutil.which('promtool'), 'promtool unavailable')
    def test_real_scans_through_prometheus_transient_and_persistent_alerts(self):
        for transient in (True, False):
            with self.subTest(transient=transient), tempfile.TemporaryDirectory(dir=ROOT / '.build') as directory:
                config = config_for('astra', resolve(self.inventory, 'astra'), str(Path(directory) / 'status.json'))
                series = {}
                cache = None
                for at in range(0, 1081, 30):
                    # 12:00 held; 12:05 unheld; 12:10 held again or persistently unheld.
                    if at in (60, 360, 660, 960):
                        restored = at == 60 or (transient and at >= 660)
                        commands = FixtureRunner(self.inputs_for('astra', restored), 'exporter')
                        exporter.scan(config, commands, commands.read, lambda: at, IDENTITY, str(Path(directory) / 'lock'))
                        cache = exporter.read_cache(config, IDENTITY)
                    metrics = exporter.metrics(config, cache, at)
                    samples = {}
                    for line in metrics.splitlines():
                        if line.startswith('#'):
                            continue
                        name, value = line.rsplit(' ', 1)
                        labels = 'job="hold",instance="host"'
                        name = name[:-1] + ',' + labels + '}' if '{' in name else name + '{' + labels + '}'
                        samples[name] = value
                    for name in samples:
                        series.setdefault(name, ['_'] * (at // 30))
                    for name, values in series.items():
                        values.append(samples.get(name, 'stale'))
                expected = [] if transient else [{'exp_labels': {'job': 'hold', 'instance': 'host', 'package_id': 'b',
                            'package': 'free-deb', 'method': 'dpkg_selections', 'severity': 'warning'}}]
                test = {'rule_files': [str(ROOT / 'monitoring/prometheus/package-hold.rules.yml')],
                        'evaluation_interval': '30s', 'tests': [{'interval': '30s',
                        'input_series': [{'series': name, 'values': ' '.join(values)} for name, values in series.items()] +
                                        [{'series': 'up{job="hold",instance="host"}', 'values': '1+0x36'}],
                        'alert_rule_test': [{'eval_time': '15m', 'alertname': 'PackageHoldPersistentUnheld', 'exp_alerts': []},
                                            {'eval_time': '18m', 'alertname': 'PackageHoldPersistentUnheld', 'exp_alerts': expected}]}]}
                path = Path(directory) / 'rules.test.yml'
                path.write_text(yaml.safe_dump(test), encoding='utf-8')
                response = subprocess.run(['promtool', 'test', 'rules', str(path)], capture_output=True, text=True, check=False)
                self.assertEqual(response.returncode, 0, response.stdout + response.stderr)
