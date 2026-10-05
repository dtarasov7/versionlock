#!/usr/bin/env python3
"""Run available local checks, keeping generated files inside the workspace."""
import ast
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    build = root / '.build'
    environment = dict(os.environ, PYTHONPYCACHEPREFIX=str(build / 'pycache'),
                       ANSIBLE_LOCAL_TEMP=str(build / 'ansible-tmp'), XDG_CACHE_HOME=str(build / 'cache'))
    # ast.parse also enforces the advertised Python 3.8 grammar on newer Python.
    python_files = [root / 'package-hold-exporter.py']
    for folder in ('library', 'module_utils', 'filter_plugins', 'scripts', 'tests'):
        python_files += list((root / folder).rglob('*.py'))
    for path in python_files:
        source = path.read_text(encoding='utf-8')
        ast.parse(source, filename=str(path), feature_version=(3, 8))
        compile(source, str(path), 'exec')
    commands = [[sys.executable, '-m', 'unittest', 'discover', '-s', 'tests/unit', '-v']]
    if shutil.which('promtool'):
        commands += [['promtool', 'check', 'rules', 'monitoring/prometheus/package-hold.rules.yml'],
                     ['promtool', 'test', 'rules', 'monitoring/prometheus/package-hold.test.yml']]
    if shutil.which('ansible-playbook'):
        for playbook in ['playbooks/report.yml', 'playbooks/exporter.yml', 'examples/specification.yml', 'examples/roles.yml']:
            commands.append(['ansible-playbook', '--syntax-check', '-i', 'examples/inventory/hosts.yml', playbook])
        commands.append([sys.executable, 'tests/integration/report-unreachable.py'])
    for executable, arguments in [('yamllint', ['.']), ('ansible-lint', ['--offline', 'roles', 'playbooks', 'examples/specification.yml', 'examples/roles.yml'])]:
        if shutil.which(executable):
            commands.append([executable] + arguments)
        else:
            print('UNAVAILABLE: ' + executable, flush=True)
    for command in commands:
        print('RUN: ' + ' '.join(command), flush=True)
        result = subprocess.run(command, cwd=root, env=environment, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
