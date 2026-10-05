"""Validate schemas, monitoring artifacts and their executable syntax."""
import copy
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[2]


def read_json(relative_path):
    source = (ROOT / relative_path).read_text(encoding="utf-8")
    return json.loads(source)


class SpecificationTests(unittest.TestCase):
    def test_schemas_are_valid(self):
        for path in (ROOT / "schemas").glob("*.json"):
            with self.subTest(schema=path.name):
                document = json.loads(path.read_text())
                jsonschema.Draft202012Validator.check_schema(document)

    def test_inventory_and_config_examples(self):
        inventory_path = ROOT / "examples/inventory/group_vars/all/package_hold.yml"
        inventory = yaml.safe_load(inventory_path.read_text())
        inventory_schema = read_json("schemas/inventory.schema.json")
        jsonschema.validate(inventory, inventory_schema)
        config = read_json("examples/exporter-config.json")
        config_schema = read_json("schemas/exporter-config.schema.json")
        jsonschema.validate(config, config_schema)
        self.assertLess(config["scan_timeout_seconds"], config["scan_interval_seconds"])
        self.assertEqual(inventory["package_hold_packages"][1]["names"]["astra"],
                         config["packages"][0]["name"])

    def test_missing_mapping_and_unsafe_name_are_invalid(self):
        schema = read_json("schemas/inventory.schema.json")
        baseline = {"package_hold_packages": [{"id": "nginx", "names": {
            "redos": "nginx", "astra": "nginx", "alt": "nginx"}}]}
        for change in ["missing", "shell", "all_null", "version_field"]:
            candidate = copy.deepcopy(baseline)
            package = candidate["package_hold_packages"][0]
            if change == "missing":
                del package["names"]["alt"]
            elif change == "shell":
                package["names"]["alt"] = "nginx;touch /tmp/x"
            elif change == "all_null":
                package["names"] = dict.fromkeys(["redos", "astra", "alt"])
            else:
                package["expected_version"] = "1"
            with self.subTest(change=change), self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(candidate, schema)

    def test_return_and_cache_examples(self):
        for name in ["status", "module-result"]:
            document = read_json("tests/fixtures/" + name + ".json")
            schema = read_json("schemas/" + name + ".schema.json")
            jsonschema.validate(document, schema)

    def test_fixture_coverage_and_absent_rows(self):
        fixtures = read_json("tests/fixtures/backend-cases.json")
        self.assertIn("Synthetic", fixtures["provenance"])
        cases = fixtures["cases"]
        for os in ["redos", "astra", "alt"]:
            for state in ["held", "unheld", "absent", "malformed", "missing_prerequisite"]:
                case = next(case for case in cases if case["case"] == os + "_" + state)
                if state == "absent":
                    self.assertEqual(case["expected_packages"], [])
                    self.assertIsNone(case["expected_host_error"])
                elif state in ["held", "unheld"]:
                    self.assertEqual(case["expected_packages"][0]["state"], state)
                else:
                    self.assertTrue(case["expected_host_error"] or
                                    case["expected_packages"][0]["state"] == "unknown")

    def test_dashboard_queries_and_datasource(self):
        dashboard = read_json("monitoring/grafana/package-hold.json")
        variables = dashboard["templating"]["list"]
        self.assertEqual(variables[0]["name"], "datasource")
        self.assertEqual(variables[-1]["name"], "interval")
        self.assertEqual(dashboard["timezone"], "browser")
        for panel in dashboard["panels"]:
            self.assertEqual(panel["datasource"]["uid"], "$datasource")
            for target in panel["targets"]:
                self.assertNotIn("version", target["expr"])
                self.assertNotIn("`", target["expr"])

    @unittest.skipUnless(shutil.which('promtool'), 'promtool unavailable')
    def test_dashboard_promql_is_accepted_by_prometheus(self):
        dashboard = read_json('monitoring/grafana/package-hold.json')
        rules = []
        for panel in dashboard['panels']:
            for target in panel['targets']:
                expression = re.sub(r'\$(?:instance|package_id|job|env|group)', '.*', target['expr'])
                rules.append({'record': 'review_panel_' + str(panel['id']) + '_' + target['refId'],
                              'expr': expression})
        with tempfile.TemporaryDirectory(dir=ROOT / '.build') as directory:
            path = Path(directory) / 'dashboard-rules.yml'
            path.write_text(yaml.safe_dump({'groups': [{'name': 'dashboard', 'rules': rules}]}), encoding='utf-8')
            result = subprocess.run(['promtool', 'check', 'rules', str(path)], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_zabbix_prototypes_share_snapshot(self):
        path = ROOT / "monitoring/zabbix/package-hold.yaml"
        export = yaml.safe_load(path.read_text())["zabbix_export"]
        template = export["templates"][0]
        discovery = template["discovery_rules"][0]
        self.assertEqual(discovery["master_item"]["key"], "package_hold.snapshot")
        self.assertEqual(discovery["enabled_lifetime_type"], "DISABLE_IMMEDIATELY")
        for item in discovery["item_prototypes"]:
            self.assertEqual(item["master_item"]["key"], "package_hold.snapshot")
        parameter = (ROOT / "monitoring/zabbix/package-hold.conf").read_text()
        self.assertNotIn("$1", parameter)
        self.assertIn("zabbix-snapshot", parameter)

    def test_zabbix_uuids_and_trigger_item_references(self):
        document = yaml.safe_load((ROOT / 'monitoring/zabbix/package-hold.yaml').read_text(encoding='utf-8'))
        export = document['zabbix_export']
        template = export['templates'][0]
        prototypes = template['discovery_rules'][0]['item_prototypes']
        keys = {item['key'] for item in template['items'] + prototypes}
        identifiers = set()
        def visit(value):
            if isinstance(value, dict):
                if 'uuid' in value:
                    self.assertRegex(value['uuid'], r'^[a-f0-9]{12}4[a-f0-9]{3}[89ab][a-f0-9]{15}$')
                    self.assertNotIn(value['uuid'], identifiers)
                    identifiers.add(value['uuid'])
                if 'expression' in value:
                    references = re.findall(r'/Package hold by Zabbix agent/([^,)\s]+)', value['expression'])
                    self.assertTrue(references)
                    self.assertTrue(set(references) <= keys, references)
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(export)

    def test_new_text_artifacts_utf8_lf_and_yaml(self):
        folders = ["docs", "schemas", "examples", "monitoring", "roles", "library", "module_utils", "filter_plugins", "playbooks"]
        for folder in folders:
            for path in (ROOT / folder).rglob("*"):
                if not path.is_file() or '__pycache__' in path.parts:
                    continue
                with self.subTest(path=str(path.relative_to(ROOT))):
                    raw = path.read_bytes()
                    text = raw.decode("utf-8")
                    self.assertNotIn(b"\r", raw)
                    self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
                    if path.suffix in [".yml", ".yaml"]:
                        yaml.safe_load(text)


if __name__ == "__main__":
    unittest.main()
