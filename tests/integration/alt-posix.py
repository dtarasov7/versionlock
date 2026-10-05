#!/usr/bin/env python3
"""Compare both matchers with the installed ALT APT HoldPackage implementation."""
import ctypes
import glob
import importlib.util
import json
import locale
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
NAMES = ['bash', 'BASH', 'nginx', 'mynginx', 'nginx-extra', 'kernel123-source',
         'alsa1-source-extra', 'ALSA2-source', 'kernel-source', 'mykernel1-source',
         'libstdc++', 'name.with.dots', 'nameXwithXdots', 'digit1', 'digitd']
PATTERNS = ['^(kernel|alsa)[0-9]+-source', '^BASH$', 'nginx', '^ng.*$', '',
            '^libstdc[+][+]$', r'^name\.with\.dots$', '^name[.]with[.]dots$',
            '^digit[[:digit:]]$', '^digit[[:alpha:]]$', r'^digit\d$',
            r'^digit\w$', r'^literal\\name$', '^(bash|nginx)$']


def native_matches():
    locale.setlocale(locale.LC_ALL, 'C')
    libraries = sorted(glob.glob('/usr/lib*/libapt-pkg*.so.*'))
    library = ctypes.CDLL(libraries[0])
    configuration = ctypes.c_void_p.in_dll(library, '_config')
    initialize = library._Z13pkgInitConfigR13Configuration
    initialize.argtypes = [ctypes.c_void_p]
    initialize.restype = ctypes.c_bool
    assert initialize(configuration), 'APT config initialization failed'
    system = ctypes.c_void_p()
    initialize_system = library._Z13pkgInitSystemR13ConfigurationRP9pkgSystem
    initialize_system.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    initialize_system.restype = ctypes.c_bool
    assert initialize_system(configuration, ctypes.byref(system)), 'APT system initialization failed'
    singleton = library._ZN14RPMPackageData9SingletonEv
    singleton.restype = ctypes.c_void_p
    package_data = singleton()
    match = library._ZN14RPMPackageData11HoldPackageEPKc
    match.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    match.restype = ctypes.c_bool
    return sorted(name for name in NAMES if match(package_data, name.encode('ascii')))


def load_module(name, path):
    specification = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def verify():
    hold = load_module('hold', ROOT / 'module_utils/hold.py')
    exporter = load_module('exporter', ROOT / 'package-hold-exporter.py')
    work_directory = ROOT / '.build/alt-posix-native'
    work_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work_directory) as directory:
        config_path = Path(directory) / 'hold.conf'
        for pattern in PATTERNS:
            config_path.write_text('RPM::Hold { "' + pattern + '"; };\n', encoding='utf-8')
            environment = dict(os.environ, APT_CONFIG=str(config_path), LC_ALL='C',
                               PYTHONDONTWRITEBYTECODE='1')
            dump = subprocess.run(['apt-config', 'dump'], env=environment, check=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            assert not dump.stderr, dump.stderr
            native = subprocess.run([sys.executable, __file__, '--native'], env=environment,
                                    check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True)
            expected = set(json.loads(native.stdout))
            for parser, matcher in [(hold.parse_alt, hold.alt_matches),
                                    (exporter.alt_rules, exporter.alt_matches)]:
                patterns = parser(dump.stdout)
                assert pattern in patterns, (pattern, patterns)
                matched = matcher(patterns, NAMES)
                assert matched == expected, (pattern, matched, expected)
            print(json.dumps({'pattern': pattern, 'held': sorted(expected)}), flush=True)
        # APT configuration keys are case insensitive; named children are holds too.
        for key in ['RPM::Hold::admin', 'rpm::hold::', 'rPm::hOlD::Admin']:
            config_path.write_text(key + ' "^bash$";\n', encoding='utf-8')
            environment = dict(os.environ, APT_CONFIG=str(config_path), LC_ALL='C',
                               PYTHONDONTWRITEBYTECODE='1')
            dump = subprocess.run(['apt-config', 'dump'], env=environment, check=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            native = subprocess.run([sys.executable, __file__, '--native'], env=environment,
                                    check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            expected = set(json.loads(native.stdout))
            assert 'bash' in expected, (key, expected)
            for parser, matcher in [(hold.parse_alt, hold.alt_matches), (exporter.alt_rules, exporter.alt_matches)]:
                assert matcher(parser(dump.stdout), NAMES) == expected, key
            print('PASS named/case-insensitive key: ' + key, flush=True)
        # A -c file loads after the default fragments and can clear their list.
        config_path.write_text('#clear RPM::Hold;\nRPM::Hold { "^bash$"; };\n', encoding='utf-8')
        cleared = subprocess.run(['apt-config', '-c', str(config_path), 'dump'],
                                 check=True, stdout=subprocess.PIPE, text=True)
        assert hold.parse_alt(cleared.stdout) == ['^bash$']
        assert exporter.alt_rules(cleared.stdout) == ['^bash$']

        class ProfileRunner(hold.Runner):
            path = hold.Runner.executable

            def run(self, argv, query=True):
                if argv == ['apt-config', 'dump']:
                    environment = dict(os.environ, APT_CONFIG=str(owned_path), LC_ALL='C')
                    response = subprocess.run(['apt-config', '-c', str(config_path), 'dump'],
                                              env=environment, check=True, text=True,
                                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    assert not response.stderr, response.stderr
                    return response.stdout
                return super().run(argv, query)

            query = run

        owned_path = Path(directory) / '90-package-hold.conf'
        hold.ALT_FRAGMENT = str(owned_path)
        original_lock = hold.mutation_lock
        hold.mutation_lock = lambda timeout: original_lock(timeout, str(Path(directory) / 'lock'))
        config_path.write_text('', encoding='utf-8')
        packages = [{'id': 'bash', 'name': 'bash'}]
        adapter = hold.Backend('alt_rpm_hold', ProfileRunner())

        def execute(state, check=False):
            result = hold.execute(packages, 'alt_rpm_hold', state, check, adapter)
            observed = exporter.query_packages({'backend': 'alt_rpm_hold', 'packages': packages},
                                               ProfileRunner())
            assert observed[0]['state'] == result['packages'][0]['state'], (result, observed)
            return result

        assert execute('query')['packages'][0]['state'] == 'unheld'
        held = execute('held')
        assert not held['failed'] and held['changed'], held
        assert not execute('held')['changed']
        planned = execute('unheld', True)
        assert planned['changed'] and planned['packages'][0]['state'] == 'held', planned
        assert owned_path.exists()
        unheld = execute('unheld')
        assert not unheld['failed'] and unheld['changed'], unheld
        assert not execute('unheld')['changed']
        assert not owned_path.exists()
        assert not execute('held')['failed']
        config_path.write_text('RPM::Hold { "^BA[[:alpha:]]+$"; };\n', encoding='utf-8')
        conflict = execute('unheld')
        assert conflict['changed'] and conflict['failed'], conflict
        assert conflict['packages'][0]['state'] == 'held', conflict
        assert conflict['packages'][0]['error']['code'] == 'external_hold_conflict', conflict
        assert not owned_path.exists()
        repeated = execute('unheld')
        assert repeated['failed'] and not repeated['changed'], repeated
    print('PASS: {} native patterns, {} names, both adapters, late clear, hold/unhold/conflict'.format(
        len(PATTERNS), len(NAMES)), flush=True)


if __name__ == '__main__':
    if sys.argv[1:] == ['--native']:
        print(json.dumps(native_matches()))
    else:
        verify()
