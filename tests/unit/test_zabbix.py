"""Execute template preprocessing and trigger arithmetic, without a Zabbix server."""
import copy
import json
import re
import shutil
import subprocess
import unittest

import yaml
from support import ROOT
import test_exporter
from support import exporter


@unittest.skipUnless(shutil.which('node'), 'node unavailable for template JavaScript checks')
class ZabbixTests(unittest.TestCase):
    def setUp(self):
        document = yaml.safe_load((ROOT / 'monitoring/zabbix/package-hold.yaml').read_text(encoding='utf-8'))
        self.export = document['zabbix_export']
        self.discovery = self.export['templates'][0]['discovery_rules'][0]

    def javascript(self, body, payload):
        script = ('const input = JSON.parse(process.argv[1]); '
                  'try { const fn = new Function("value", input.body); '
                  'process.stdout.write(JSON.stringify({result: fn(JSON.stringify(input.payload))})); } '
                  'catch(e) { process.stdout.write(JSON.stringify({error: String(e)})); }')
        response = subprocess.run(['node', '-e', script, json.dumps({'body': body, 'payload': payload})],
                                  capture_output=True, text=True, check=True)
        return json.loads(response.stdout)

    def evaluate(self, expression, values, now=1000, no_data=0):
        def function(match):
            name, key = match.group(1), match.group(2)
            samples = values.get(key, [0])
            return str({'last': samples[-1], 'max': max(samples), 'min': min(samples), 'nodata': no_data}[name])
        expression = re.sub(r'(last|max|min|nodata)\(/Package hold by Zabbix agent/package_hold\.([^,)]+)(?:,[^)]+)?\)', function, expression)
        expression = expression.replace('now()', str(now)).replace(' and ', ' && ').replace(' or ', ' || ')
        expression = re.sub(r'(?<![<>=!])=(?!=)', '===', expression)
        return self.javascript('return ' + expression + ';', {})['result']

    def test_discovery_discards_unknown_inventory_but_accepts_confirmed_absence(self):
        step = self.discovery['preprocessing'][0]
        self.assertEqual(step['type'], 'JAVASCRIPT')
        self.assertEqual(step['error_handler'], 'DISCARD_VALUE')
        for payload in [{'discovery_valid': 0, 'packages': []}, {'packages': []}]:
            self.assertIn('error', self.javascript(step['parameters'][0], payload))
        result = self.javascript(step['parameters'][0], {'discovery_valid': 1, 'packages': []})
        self.assertEqual(json.loads(result['result']), [])
        packages = [{'id': 'a'}, {'id': 'b'}]
        result = self.javascript(step['parameters'][0], {'discovery_valid': 1, 'packages': packages})
        self.assertEqual(json.loads(result['result']), packages)

    def test_marker_does_not_immediately_fire_stale_trigger(self):
        trigger = next(item for item in self.export['triggers'] if item['name'] == 'Scanner stale')
        self.assertFalse(self.evaluate(trigger['expression'], {'fresh': [1, 0], 'last_completed': [0], 'stale_after': [662]}))
        self.assertTrue(self.evaluate(trigger['expression'], {'fresh': [1], 'last_completed': [100], 'stale_after': [662]}))
        self.assertTrue(self.evaluate(trigger['expression'], {'fresh': [0, 0], 'last_completed': [0], 'stale_after': [662]}))

    def test_persistent_trigger_suppresses_stale_future_and_transport_failure(self):
        item = next(item for item in self.discovery['item_prototypes'] if item['key'].startswith('package_hold.persistent['))
        expression = item['trigger_prototypes'][0]['expression']
        values = {'persistent[{#PACKAGE_ID}]': [1, 1, 1], 'fresh': [1], 'cache_valid': [1],
                  'last_completed': [900], 'stale_after': [662]}
        self.assertTrue(self.evaluate(expression, values))
        for timestamp in (100, 1100):
            changed = dict(values, last_completed=[timestamp])
            self.assertFalse(self.evaluate(expression, changed))
        self.assertFalse(self.evaluate(expression, values, no_data=1))
        self.assertFalse(self.evaluate(expression, dict(values, **{'persistent[{#PACKAGE_ID}]': [0, 0, 0]})))

    def test_missing_and_unknown_samples_never_become_unheld(self):
        for item in self.discovery['item_prototypes']:
            step = item['preprocessing'][0]
            body = step['parameters'][0].replace('{#PACKAGE_ID}', 'a')
            result = self.javascript(body, {'packages': []})
            if item['key'].startswith('package_hold.persistent['):
                self.assertEqual(result['result'], 0)
            else:
                self.assertIn('error', result)
            if item['key'].startswith('package_hold.status['):
                result = self.javascript(body, {'packages': [{'id': 'a', 'status': None}]})
                self.assertIn('error', result)


class DiscoveryCacheTests(unittest.TestCase):
    setUp = test_exporter.ExporterTests.setUp
    scan = test_exporter.ExporterTests.scan
    def test_discovery_valid_distinguishes_failed_inventory_from_empty(self):
        _, cache = self.scan()
        self.assertEqual(exporter.snapshot(self.config, cache, 1000)['discovery_valid'], 1)
        self.assertEqual(exporter.snapshot(self.config, cache, 2000)['discovery_valid'], 0)
        broken = copy.deepcopy(self.fixture)
        broken['installed_query']['rc'] = 2
        _, cache = self.scan(1300, broken)
        self.assertEqual(exporter.snapshot(self.config, cache, 1300)['discovery_valid'], 0)
        empty = copy.deepcopy(self.fixture)
        empty['installed_query']['stdout'] = ''
        _, cache = self.scan(1600, empty)
        self.assertEqual(exporter.snapshot(self.config, cache, 1600)['discovery_valid'], 1)
        unknown = copy.deepcopy(self.fixture)
        unknown['prerequisite_available'] = False
        _, cache = self.scan(1900, unknown)
        self.assertEqual(exporter.snapshot(self.config, cache, 1900)['discovery_valid'], 1)
