import os
import json
from pathlib import Path
import stat
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from support import ROOT, exporter, hold


class RunnerTests(unittest.TestCase):
    def runners(self, timeout=1):
        return [('module', hold.Runner(timeout)),
                ('exporter', exporter.Commands(timeout, time.monotonic() + timeout + 1))]

    def query(self, consumer, runner, source):
        method = runner.run if consumer == 'module' else runner.query
        path_method = 'executable' if consumer == 'module' else 'path'
        with patch.object(runner, path_method, return_value=sys.executable):
            return method(['python3', '-c', source])

    def test_real_process_success_clean_environment_argument_arrays(self):
        for consumer, runner in self.runners():
            with self.subTest(consumer=consumer), patch.dict(os.environ, {'APT_CONFIG': '/unsafe', 'DPKG_ROOT': '/unsafe'}):
                output = self.query(consumer, runner, 'import os; print(os.environ.get("APT_CONFIG")); print(os.environ["LC_ALL"])')
                self.assertEqual(output, 'None\nC\n')

    def test_nonzero_stderr_invalid_utf8_timeout_and_output_cap(self):
        cases = [('import sys; sys.exit(2)', 'command_failed'),
                 ('import sys; sys.stderr.write("permission denied")', 'permission_denied'),
                 ('import sys; sys.stdout.buffer.write(b"\\xff")', 'malformed_output'),
                 ('import time; time.sleep(5)', 'command_timeout'),
                 ('print("x" * (16 * 1024 * 1024))', 'malformed_output')]
        for source, expected in cases:
            for consumer, runner in self.runners(timeout=0.3):
                error_type = hold.HoldError if consumer == 'module' else exporter.ScanError
                with self.subTest(consumer=consumer, expected=expected), self.assertRaises(error_type) as caught:
                    self.query(consumer, runner, source)
                detail = caught.exception.error if consumer == 'module' else caught.exception.detail
                self.assertEqual(detail['code'], expected)

    def test_scan_deadline_limits_per_command_timeout(self):
        runner = exporter.Commands(10, time.monotonic() + 0.05)
        start = time.monotonic()
        with self.assertRaises(exporter.ScanError) as caught:
            self.query('exporter', runner, 'import time; time.sleep(2)')
        self.assertEqual(caught.exception.detail['code'], 'command_timeout')
        self.assertLess(time.monotonic() - start, 1)

    def test_dnf_query_writes_only_disposable_scratch_and_preserves_config(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.build') as directory:
            executable = Path(directory) / 'dnf'
            executable.write_text('#!' + sys.executable + '\nimport json, sys\nfrom pathlib import Path\n'
                                  'for argument in sys.argv[1:]:\n'
                                  '    if argument.startswith("--setopt="):\n'
                                  '        key, path = argument[len("--setopt="):].split("=", 1)\n'
                                  '        Path(path, key).write_text("probe")\n'
                                  'print(json.dumps(sys.argv[1:]))\n', encoding='utf-8')
            executable.chmod(0o700)
            for consumer, runner in self.runners():
                method = runner.run if consumer == 'module' else runner.query
                path_method = 'executable' if consumer == 'module' else 'path'
                with self.subTest(consumer=consumer), patch.object(runner, path_method, return_value=str(executable)), patch.object(tempfile, 'tempdir', directory):
                    response = method(['dnf', '-q', '--disablerepo=*', 'versionlock', 'list'])
                arguments = json.loads(response)
                self.assertEqual(arguments[3:], ['-q', '--disablerepo=*', 'versionlock', 'list'])
                for key, argument in zip(('logdir', 'cachedir', 'persistdir'), arguments[:3]):
                    self.assertTrue(argument.startswith('--setopt=' + key + '='))
                    scratch = Path(argument.split('=', 2)[2])
                    self.assertEqual(scratch.parent, Path(directory))
                    self.assertFalse(scratch.exists())
                self.assertFalse(any('locklist' in arg or 'installroot' in arg for arg in arguments))

    def test_fifo_cache_does_not_block_reader(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.build') as directory:
            path = str(Path(directory) / 'fifo')
            os.mkfifo(path)
            start = time.monotonic()
            with self.assertRaises(exporter.ScanError):
                exporter.load_text(path)
            self.assertLess(time.monotonic() - start, 0.5)

    def test_os_detection_strict(self):
        for os_id, normalized in [('redos', 'redos'), ('astra', 'astra'), ('altlinux', 'alt')]:
            output = 'ID="' + os_id + '"\nVERSION_ID="1.7"\n'
            self.assertEqual(hold.detect_os(lambda path: output)['id'], normalized)
            self.assertEqual(exporter.current_os(lambda path: output), normalized)
        for output in ['ID=debian\nVERSION_ID=12\n', 'ID=astra\n', 'ID=astra\nID=redos\nVERSION_ID=1\n']:
            with self.assertRaises(hold.HoldError):
                hold.detect_os(lambda path: output)
            with self.assertRaises(exporter.ScanError):
                exporter.current_os(lambda path: output)


if __name__ == '__main__':
    unittest.main()
