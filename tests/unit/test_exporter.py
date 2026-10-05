import copy
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen
from support import CASES, FixtureRunner, ROOT, config_for, exporter

IDENTITY = '11111111-1111-4111-8111-111111111111'


class ExporterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / '.build')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.config = config_for(status_path=str(self.directory / 'status.json'))
        self.lock = str(self.directory / 'scan.lock')
        self.fixture = copy.deepcopy(next(case for case in CASES if case['case'] == 'astra_unheld')['inputs'])
        self.fixture['os_id'] = 'astra'

    def scan(self, at=1000, inputs=None):
        runner = FixtureRunner(inputs or self.fixture, 'exporter')
        result = exporter.scan(self.config, runner, runner.read, lambda: at, IDENTITY, self.lock)
        return result, exporter.read_cache(self.config, IDENTITY)

    def test_scan_success_and_status_schema(self):
        import jsonschema
        result, cache = self.scan()
        self.assertEqual(result, 0)
        schema = json.loads((ROOT / 'schemas/status.schema.json').read_text())
        jsonschema.validate(cache, schema)
        self.assertEqual(cache['last_success_at'], 1000)
        self.assertEqual(cache['packages'][0]['unheld_streak'], 1)
        self.assertFalse(cache['packages'][0]['persistent_unheld'])

    def test_three_scans_required_polling_does_not_increment(self):
        _, cache = self.scan(1000)
        for _ in range(100):
            payload = exporter.metrics(self.config, cache, 1100)
            self.assertIn('package_hold_persistent_unheld{package_id="nginx",package="nginx",method="dpkg_selections"} 0', payload)
        _, cache = self.scan(1300)
        self.assertEqual(cache['packages'][0]['unheld_streak'], 2)
        self.assertFalse(cache['packages'][0]['persistent_unheld'])
        _, cache = self.scan(1600)
        self.assertTrue(cache['packages'][0]['persistent_unheld'])
        self.assertEqual(cache['packages'][0]['unheld_since'], 1000)

    def test_held_error_failed_scan_gap_and_disappearance_reset_streak(self):
        for kind in ['held', 'unknown', 'failure', 'gap', 'absent']:
            with self.subTest(kind=kind):
                _, cache = self.scan(2000)
                middle = copy.deepcopy(self.fixture)
                if kind == 'held':
                    middle['installed_query']['stdout'] = 'nginx\tamd64\thold ok installed\n'
                elif kind == 'unknown':
                    middle['prerequisite_available'] = False
                elif kind == 'failure':
                    middle['installed_query']['rc'] = 2
                elif kind == 'absent':
                    middle['installed_query']['stdout'] = ''
                if kind != 'gap':
                    self.scan(2300, middle)
                _, final = self.scan(3000 if kind == 'gap' else 2600)
                self.assertEqual(final['packages'][0]['unheld_streak'], 1)
                self.assertFalse(final['packages'][0]['persistent_unheld'])
                Path(self.config['status_path']).unlink()

    def test_digest_boot_and_clock_reset_or_stale(self):
        _, cache = self.scan()
        self.assertIsNone(exporter.read_cache(self.config, '22222222-2222-4222-8222-222222222222'))
        changed = dict(self.config, scan_interval_seconds=600)
        self.assertIsNone(exporter.read_cache(changed, IDENTITY))
        self.assertFalse(exporter.cache_fresh(cache, self.config, 999))
        self.assertFalse(exporter.cache_fresh(cache, self.config, 1663))
        self.assertNotIn('package_hold_status{', exporter.metrics(self.config, cache, 1663))
        self.assertEqual(exporter.snapshot(self.config, cache, 1663)['packages'], [])

    def test_empty_installed_success_no_rows_metrics_or_discovery(self):
        inputs = copy.deepcopy(self.fixture)
        inputs['installed_query']['stdout'] = ''
        inputs['prerequisite_available'] = False
        result, cache = self.scan(inputs=inputs)
        self.assertEqual(result, 0)
        self.assertEqual(cache['packages'], [])
        self.assertNotIn('package_hold_status{', exporter.metrics(self.config, cache, 1001))
        self.assertEqual(exporter.snapshot(self.config, cache, 1001)['packages'], [])

    def test_partial_error_keeps_good_installed_row_unknown_has_no_zero(self):
        self.config['packages'].append({'id': 'good', 'name': 'good'})
        inputs = copy.deepcopy(self.fixture)
        inputs['installed_query']['stdout'] = 'nginx\tamd64\thold reinstreq installed\ngood\tamd64\thold ok installed\n'
        result, cache = self.scan(inputs=inputs)
        self.assertEqual(result, 1)
        self.assertFalse(cache['scan_success'])
        self.assertEqual(cache['last_success_at'], 0)
        payload = exporter.metrics(self.config, cache, 1001)
        self.assertNotIn('package_hold_status{package_id="nginx"', payload)
        self.assertIn('package_hold_check_success{package_id="nginx",package="nginx",method="dpkg_selections"} 0', payload)
        self.assertIn('package_hold_status{package_id="good",package="good",method="dpkg_selections"} 1', payload)
        self.assertIsNone(exporter.snapshot(self.config, cache, 1001)['packages'][0]['status'])

    def test_total_failure_replaces_rows_preserves_last_success(self):
        self.scan()
        inputs = copy.deepcopy(self.fixture)
        inputs['installed_query']['rc'] = 2
        result, cache = self.scan(1300, inputs)
        self.assertEqual(result, 1)
        self.assertEqual(cache['packages'], [])
        self.assertEqual(cache['last_success_at'], 1000)
        self.assertEqual(cache['last_completed_at'], 1300)
        self.assertEqual(cache['errors'][0]['code'], 'command_failed')

    def test_attempt_marker_survives_aborted_scan(self):
        self.scan()
        with patch.object(exporter, 'query_packages', side_effect=RuntimeError('crash')):
            with self.assertRaises(RuntimeError):
                self.scan(1300)
        cache = exporter.read_cache(self.config, IDENTITY)
        self.assertEqual(cache['packages'], [])
        self.assertEqual(cache['last_completed_at'], 0)
        self.assertEqual(cache['last_success_at'], 1000)
        self.assertFalse(cache['scan_success'])

    def test_atomic_failure_preserves_old_file_and_cleans_temporary(self):
        self.scan()
        path = Path(self.config['status_path'])
        old = path.read_bytes()
        with patch.object(exporter.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                exporter.atomic_json(str(path), {'new': True})
        self.assertEqual(path.read_bytes(), old)
        self.assertEqual(list(self.directory.glob('.status-*')), [])

    def test_atomic_fsync_before_and_after_replace(self):
        calls = []
        original_fsync, original_replace = os.fsync, os.replace
        def sync(fd):
            calls.append('sync')
            return original_fsync(fd)
        def replace(source, target):
            calls.append('replace')
            return original_replace(source, target)
        with patch.object(exporter.os, 'fsync', side_effect=sync), patch.object(exporter.os, 'replace', side_effect=replace):
            exporter.atomic_json(self.config['status_path'], {'valid': True})
        self.assertEqual(calls, ['sync', 'replace', 'sync'])

    def test_duplicate_scan_lock_does_not_touch_cache(self):
        self.scan()
        old = Path(self.config['status_path']).read_bytes()
        with open(self.lock, 'r+') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            result, _ = self.scan(1300)
        self.assertEqual(result, 3)
        self.assertEqual(Path(self.config['status_path']).read_bytes(), old)

    def test_missing_corrupt_and_invalid_semantic_cache_health_only(self):
        self.assertIsNone(exporter.read_cache(self.config, IDENTITY))
        for content in ['{', 'null', '{"a":1,"a":2}', '{"schema_version":NaN}', '[' * 2000 + '0' + ']' * 2000]:
            Path(self.config['status_path']).write_text(content)
            self.assertIsNone(exporter.read_cache(self.config, IDENTITY))
        _, baseline = self.scan()
        changes = [('scan_id', None), ('last_completed_at', float('nan')), ('scan_success', 1),
                   ('config_digest', 'bad'), ('schema_version', True)]
        for key, value in changes:
            cache = copy.deepcopy(baseline)
            cache[key] = value
            with self.subTest(key=key), self.assertRaises((ValueError, TypeError)):
                exporter.validate_cache(cache, self.config, IDENTITY)
        payload = exporter.metrics(self.config, None, 1000)
        self.assertIn('package_hold_cache_valid 0', payload)
        self.assertNotIn('package_hold_status{', payload)
        self.assertEqual(exporter.snapshot(self.config, None)['packages'], [])

    def test_config_validation(self):
        for key, value in [('scan_interval_seconds', True), ('backend', 'alt_rpm_hold'),
                           ('scan_timeout_seconds', 300), ('unknown', 1), ('status_path', 'relative')]:
            config = dict(self.config, **{key: value})
            with self.subTest(key=key), self.assertRaises(exporter.ScanError):
                exporter.check_config(config)
        with self.assertRaises(ValueError):
            exporter.parse_json('{"os":"astra","os":"alt"}')

    def test_label_escaping(self):
        self.assertEqual(exporter.label_escape('a"b\\c\nd'), 'a\\"b\\\\c\\nd')

    def test_http_metrics_health_and_corrupt_cache_no_commands(self):
        _, cache = self.scan(time.time())
        # Reader needs host identity; use patched boot_id without calling package tools.
        with patch.object(exporter, 'boot_id', return_value=IDENTITY), patch.object(exporter.Commands, 'query', side_effect=AssertionError('HTTP executed command')):
            server = exporter.CacheHTTPServer(('127.0.0.1', 0), self.config)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            address = 'http://127.0.0.1:' + str(server.server_port)
            try:
                with urlopen(address + '/metrics', timeout=5) as response:
                    payload = response.read().decode()
                    self.assertEqual(response.status, 200)
                    self.assertIn('version=0.0.4', response.headers['Content-Type'])
                    self.assertIn('package_hold_status{package_id="nginx",package="nginx",method="dpkg_selections"} 0', payload)
                Path(self.config['status_path']).write_text('broken')
                with urlopen(address + '/metrics', timeout=5) as response:
                    self.assertIn(b'package_hold_cache_valid 0', response.read())
                with urlopen(address + '/healthz', timeout=5) as response:
                    self.assertEqual(response.read(), b'ok\n')
                with self.assertRaises(HTTPError) as caught:
                    urlopen(address + '/missing', timeout=5)
                self.assertEqual(caught.exception.code, 404)
            finally:
                server.shutdown()
                thread.join(5)
                server.server_close()

    def test_cli_snapshot_discovery_get_and_sigterm(self):
        # Real CLI subprocess, with cache carrying the actual boot identity.
        identity = exporter.boot_id()
        runner = FixtureRunner(self.fixture, 'exporter')
        self.assertEqual(exporter.scan(self.config, runner, runner.read, time.time, identity, self.lock), 0)
        config_path = self.directory / 'config.json'
        config_path.write_text(json.dumps(self.config))
        command = [sys.executable, str(ROOT / 'package-hold-exporter.py')]
        for subcommand in ('zabbix-snapshot', 'zabbix-discovery', 'zabbix-get'):
            argv = command + [subcommand, '--config', str(config_path)]
            if subcommand == 'zabbix-get':
                argv += ['--package-id', 'nginx', '--field', 'status']
            response = subprocess.run(argv, capture_output=True, text=True, timeout=10)
            self.assertEqual(response.returncode, 0, response.stderr)
            payload = json.loads(response.stdout)
            if subcommand == 'zabbix-discovery':
                self.assertEqual(payload[0]['{#PACKAGE_ID}'], 'nginx')
            elif subcommand == 'zabbix-snapshot':
                self.assertEqual(payload['packages'][0]['status'], 0)
            else:
                self.assertEqual(payload, 0)
        missing = subprocess.run(command + ['zabbix-get', '--config', str(config_path), '--package-id', 'missing', '--field', 'status'], capture_output=True, text=True, timeout=10)
        self.assertEqual(missing.returncode, 1)
        Path(self.config['status_path']).write_text('broken')
        invalid = subprocess.run(command + ['zabbix-snapshot', '--config', str(config_path)], capture_output=True, text=True, timeout=10)
        self.assertEqual(invalid.returncode, 0)
        self.assertEqual(json.loads(invalid.stdout)['cache_valid'], 0)
        process = subprocess.Popen(command + ['serve', '--config', str(config_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            time.sleep(0.2)
            process.send_signal(signal.SIGTERM)
            output, diagnostic = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, diagnostic)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_concurrent_http_readers_never_observe_partial_atomic_writes(self):
        _, cache = self.scan(time.time())
        with patch.object(exporter, 'boot_id', return_value=IDENTITY), patch.object(exporter.Commands, 'query', side_effect=AssertionError('HTTP scan')):
            server = exporter.CacheHTTPServer(('127.0.0.1', 0), self.config)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            address = 'http://127.0.0.1:' + str(server.server_port) + '/metrics'
            def write_cache():
                for _ in range(80):
                    exporter.atomic_json(self.config['status_path'], cache)
            def read_metrics():
                with urlopen(address, timeout=5) as response:
                    return response.read().decode('utf-8')
            try:
                with ThreadPoolExecutor(max_workers=8) as pool:
                    writer = pool.submit(write_cache)
                    readers = [pool.submit(read_metrics) for _ in range(40)]
                    for reader in readers:
                        self.assertIn('package_hold_cache_valid 1\n', reader.result())
                    writer.result()
            finally:
                server.shutdown()
                thread.join(5)
                server.server_close()

    def test_http_slow_clients_are_bounded_and_slots_recover(self):
        with patch.object(exporter.CacheHTTPServer, 'max_clients', 2):
            server = exporter.CacheHTTPServer(('127.0.0.1', 0), self.config)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        clients = []
        try:
            for _ in range(2):
                client = socket.create_connection(server.server_address, timeout=2)
                client.sendall(b'GET /healthz HTTP/1.1\r\n')
                clients.append(client)
            deadline = time.monotonic() + 2
            while server.client_slots._value and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(server.client_slots._value, 0)
            with socket.create_connection(server.server_address, timeout=2) as overflow:
                self.assertEqual(overflow.recv(1), b'')
            for client in clients:
                client.close()
            deadline = time.monotonic() + 2
            while server.client_slots._value != 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(server.client_slots._value, 2)
            with urlopen('http://127.0.0.1:' + str(server.server_port) + '/healthz', timeout=2) as response:
                self.assertEqual(response.read(), b'ok\n')
        finally:
            for client in clients:
                client.close()
            server.shutdown()
            thread.join(5)
            server.server_close()

    @unittest.skipUnless(socket.has_ipv6, 'IPv6 unavailable')
    def test_ipv6_bind(self):
        try:
            server = exporter.CacheHTTPServer(('::1', 0), self.config)
        except OSError as exc:
            if exc.errno in (97, 99):
                self.skipTest('IPv6 loopback unavailable')
            raise
        self.assertEqual(server.address_family, socket.AF_INET6)
        server.server_close()


if __name__ == '__main__':
    unittest.main()
