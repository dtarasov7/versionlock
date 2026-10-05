#!/usr/bin/env python3
"""Exercise native DNF deletion against private config and locklist on disposable RED OS."""
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    system_path = Path('/etc/dnf/plugins/versionlock.list')
    system_before = system_path.read_bytes()
    with tempfile.TemporaryDirectory(prefix='package-hold-native-dnf-') as directory:
        root = Path(directory)
        config = root / 'dnf.conf'
        plugin = root / 'versionlock.conf'
        locklist = root / 'versionlock.list'
        config.write_text('[main]\nplugins=1\npluginconfpath=' + directory + '\n', encoding='utf-8')
        plugin.write_text('[main]\nenabled=1\nlocklist=' + str(locklist) + '\n', encoding='utf-8')
        records = ['tool-2-0:1-1.*', 'tool-0:2-1.*', 'tool-2-extra-0:1-1.*']
        locklist.write_text('\n'.join(records) + '\n', encoding='utf-8')
        command = ['dnf', '-c', str(config), '-q', '--disablerepo=*']
        command += ['--setopt=' + key + '=' + directory for key in ('logdir', 'cachedir', 'persistdir')]
        command += ['versionlock', 'delete', records[0].replace('*', '[*]')]
        environment = dict(os.environ, LC_ALL='C')
        response = subprocess.run(command, env=environment, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, timeout=30, check=False)
        assert response.returncode == 0, (response.stdout, response.stderr)
        assert not response.stderr, response.stderr
        remaining = locklist.read_text(encoding='utf-8').splitlines()
        assert remaining == records[1:], remaining
        response = subprocess.run(command, env=environment, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, timeout=30, check=False)
        assert response.returncode == 0, (response.stdout, response.stderr)
        assert locklist.read_text(encoding='utf-8').splitlines() == remaining
    assert system_path.read_bytes() == system_before, 'System locklist changed'
    print('PASS: native DNF exact record delete, name/version collision, repeat, system locklist preserved')


if __name__ == '__main__':
    main()
