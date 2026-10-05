#!/usr/bin/env python3
"""A host lost after role initialization must fail report coverage in both phases."""
import csv
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def main():
    report_root = ROOT / '.build/unreachable-reports'
    for phase in ('hold', 'prerequisites'):
        for checked in (False, True):
            before = set(report_root.glob('*'))
            argv = [sys.executable, str(ROOT / 'scripts/run-report.py'), '-i',
                    str(ROOT / 'tests/integration/unreachable-inventory.yml'), '--phase', phase]
            if checked:
                argv.append('--check')
            with tempfile.TemporaryDirectory(dir=str(ROOT / '.build')) as working_directory:
                response = subprocess.run(argv, cwd=working_directory, stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT, text=True, timeout=60, check=False)
            if response.returncode == 0 or 'UNREACHABLE!' not in response.stdout:
                raise AssertionError(response.stdout)
            created = set(report_root.glob('*')) - before
            if checked:
                assert not created, created
            else:
                assert len(created) == 1, (created, response.stdout)
                directory = created.pop()
                with (directory / 'hosts.csv').open(encoding='utf-8', newline='') as stream:
                    coverage = list(csv.DictReader(stream))
                assert coverage and all(row['success'] == 'False' for row in coverage), coverage
                assert any(row['error_code'] == 'host_unreachable' for row in coverage), coverage
                with (directory / (phase + '.csv')).open(encoding='utf-8', newline='') as stream:
                    assert list(csv.DictReader(stream)) == []
            print('PASS unreachable after initialization: phase={}, check={}'.format(phase, checked))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
