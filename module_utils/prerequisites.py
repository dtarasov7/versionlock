"""Capability probes and bounded remediation of prerequisites only."""
from ansible.module_utils.hold import (
    BACKENDS, Backend, HoldError, Runner, detect_os, parse_alt,
)


def inspect(platform, runner, plugin_package):
    backend = BACKENDS[platform['id']]
    adapter = Backend(backend, runner)
    components = []

    def probe(name, callback):
        try:
            callback()
            components.append({'name': name, 'available': True, 'error': None})
        except HoldError as exc:
            components.append({'name': name, 'available': False, 'error': exc.error})

    programs = {'redos': ['rpm', 'dnf'], 'astra': ['dpkg-query', 'apt-mark', 'apt-get'],
                'alt': ['rpm', 'apt-config', 'apt-get']}[platform['id']]
    for program in programs:
        probe(program, lambda program=program: runner.executable(program))
    inventory = None
    try:
        inventory = adapter.installed()
        components.append({'name': 'package_database', 'available': True, 'error': None})
    except HoldError as exc:
        components.append({'name': 'package_database', 'available': False, 'error': exc.error})
    expected_packages = {'redos': ['rpm', 'dnf', plugin_package], 'astra': ['apt', 'dpkg'],
                         'alt': ['rpm', 'apt']}[platform['id']]
    for package in expected_packages:
        available = inventory is not None and package in inventory
        components.append({'name': 'package:' + package, 'available': available,
                           'error': None if available else {'code': 'prerequisite_missing',
                                                            'message': 'Required package unavailable: ' + package}})
    if backend == 'dnf_versionlock':
        probe('versionlock_configuration', adapter.dnf_config)
    elif backend == 'alt_rpm_hold':
        probe('rpm_hold_configuration', lambda: parse_alt(runner.run(['apt-config', 'dump'])))
    return components


def run_prerequisites(mode='audit', check_mode=False, timeout=30,
                      plugin_package='python3-dnf-plugin-versionlock', runner=None, platform=None):
    result = {'schema_version': 1, 'os': {'id': '', 'version_id': ''}, 'backend': None,
              'mode': mode, 'check_mode': check_mode, 'changed': False, 'success': False,
              'components': [], 'errors': []}
    runner = runner or Runner(timeout)
    try:
        platform = platform or detect_os()
        result.update(os=platform, backend=BACKENDS[platform['id']])
        before = inspect(platform, runner, plugin_package)
        after = before
        missing_packages = [component['name'][8:] for component in before
                            if component['name'].startswith('package:') and not component['available']]
        # No recovery through a missing/broken base package manager or database.
        bootstrap = {'redos': ['rpm', 'dnf', 'package_database'],
                     'astra': ['dpkg-query', 'apt-get', 'package_database'],
                     'alt': ['rpm', 'apt-get', 'package_database']}[platform['id']]
        base_ok = all(component['available'] for component in before if component['name'] in bootstrap)
        would_install = mode == 'remediate' and missing_packages and base_ok
        install_error = None
        if would_install and not check_mode:
            argv = (['dnf', '-y', 'install'] if platform['id'] == 'redos' else ['apt-get', '-y', 'install'])
            result['changed'] = True
            try:
                runner.run(argv + missing_packages, query=False)
            except HoldError as exc:
                install_error = exc.error
            # Even a partially failed install must be probed again.
            after = inspect(platform, runner, plugin_package)
        final_by_name = {component['name']: component for component in after}
        for original in before:
            final = final_by_name[original['name']]
            planned = bool(would_install and not original['available']
                           and (original['name'].startswith('package:') or
                                original['error']['code'] == 'prerequisite_missing'))
            result['components'].append({'name': original['name'], 'available_before': original['available'],
                                         'available_after': final['available'],
                                         'changed': bool(not check_mode and original['available'] != final['available']),
                                         'would_change': bool(check_mode and planned), 'error': final['error']})
        if install_error:
            result['errors'].append(install_error)
        if check_mode:
            result['changed'] = any(component['would_change'] for component in result['components'])
        result['success'] = all(component['available_after'] for component in result['components']) and not result['errors']
    except HoldError as exc:
        result['errors'].append(exc.error)
    return result
