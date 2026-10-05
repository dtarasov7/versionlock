#!/usr/bin/env python3
"""Periodic native hold scanner and cache-only monitoring interfaces (Python 3.8)."""
import argparse
import configparser
import ctypes
import errno
import fcntl
import hashlib
import http.server
import json
import logging
import math
import os
import re
import selectors
import shlex
import signal
import socket
import stat
import subprocess
import tempfile
import threading
import time
import uuid

LOG = logging.getLogger('package-hold')
METHODS = {'redos': 'dnf_versionlock', 'astra': 'dpkg_selections', 'alt': 'alt_rpm_hold'}
PACKAGE_RE = r'[A-Za-z0-9][A-Za-z0-9_.+\-]{0,254}'
CACHE_LIMIT = 4 * 1024 * 1024
COMMAND_LIMIT = 16 * 1024 * 1024


class ScanError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.detail = {'code': code, 'message': message}


def load_text(path, maximum=COMMAND_LIMIT):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ScanError('configuration_invalid', 'Regular file required: ' + path)
            content = source.read(maximum + 1)
        if len(content) > maximum:
            raise ScanError('malformed_output', 'File size limit exceeded: ' + path)
        return content.decode('utf-8', errors='strict')
    except PermissionError as exc:
        raise ScanError('permission_denied', str(exc)) from exc
    except UnicodeError as exc:
        raise ScanError('malformed_output', 'Invalid UTF-8 in ' + path) from exc
    except OSError as exc:
        raise ScanError('prerequisite_missing', str(exc)) from exc


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key: ' + key)
        result[key] = value
    return result


def parse_json(content):
    def reject_constant(value):
        raise ValueError('Nonfinite JSON number: ' + value)
    return json.loads(content, object_pairs_hook=unique_object, parse_constant=reject_constant)


def check_config(config):
    required = {'schema_version', 'os', 'backend', 'packages', 'scan_interval_seconds',
                'scan_timeout_seconds', 'command_timeout_seconds', 'listen_address',
                'listen_port', 'status_path'}
    if (not isinstance(config, dict) or set(config) != required
            or type(config['schema_version']) is not int or config['schema_version'] != 1
            or not isinstance(config['os'], str) or config['os'] not in METHODS
            or config['backend'] != METHODS[config['os']]):
        raise ScanError('configuration_invalid', 'Invalid config schema or OS/backend combination')
    for key, minimum, maximum in [('scan_interval_seconds', 60, None),
                                   ('scan_timeout_seconds', 1, None),
                                   ('command_timeout_seconds', 1, None),
                                   ('listen_port', 1024, 65535)]:
        value = config[key]
        if type(value) is not int or value < minimum or (maximum and value > maximum):
            raise ScanError('configuration_invalid', 'Invalid integer: ' + key)
    if not config['command_timeout_seconds'] <= config['scan_timeout_seconds'] < config['scan_interval_seconds']:
        raise ScanError('configuration_invalid', 'Require command timeout <= deadline < interval')
    for key in ('listen_address', 'status_path'):
        if not isinstance(config[key], str) or not config[key] or '\x00' in config[key]:
            raise ScanError('configuration_invalid', 'Invalid ' + key)
    if not os.path.isabs(config['status_path']):
        raise ScanError('configuration_invalid', 'Absolute status_path required')
    packages = config['packages']
    if not isinstance(packages, list) or len(packages) > 1024:
        raise ScanError('configuration_invalid', 'Invalid packages list')
    ids, names = set(), set()
    for item in packages:
        if not isinstance(item, dict) or set(item) != {'id', 'name'}:
            raise ScanError('configuration_invalid', 'Resolved package needs only id/name')
        package_id, name = item['id'], item['name']
        if not isinstance(package_id, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', package_id):
            raise ScanError('configuration_invalid', 'Invalid package id')
        if not isinstance(name, str) or not re.fullmatch(PACKAGE_RE, name):
            raise ScanError('configuration_invalid', 'Invalid exact package name')
        if config['os'] == 'astra' and not re.fullmatch(r'[a-z0-9][a-z0-9+.\-]+', name):
            raise ScanError('configuration_invalid', 'Invalid dpkg name')
        if package_id in ids or name in names:
            raise ScanError('configuration_invalid', 'Duplicate package id/name')
        ids.add(package_id)
        names.add(name)
    return config


def config_digest(config):
    encoded = json.dumps(config, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def boot_id():
    value = load_text('/proc/sys/kernel/random/boot_id', 64).strip()
    uuid.UUID(value)
    return value


def current_os(reader=load_text):
    values = {}
    for line in reader('/etc/os-release').splitlines():
        if not line or line.startswith('#'):
            continue
        key, equal, raw = line.partition('=')
        if not equal or key in values:
            raise ScanError('configuration_invalid', 'Invalid os-release')
        try:
            decoded = shlex.split(raw, comments=False)
        except ValueError as exc:
            raise ScanError('configuration_invalid', 'Invalid os-release quoting') from exc
        values[key] = decoded[0] if len(decoded) == 1 else ''
    normalized = {'redos': 'redos', 'astra': 'astra', 'altlinux': 'alt'}.get(values.get('ID'))
    if normalized is None or not values.get('VERSION_ID'):
        raise ScanError('unsupported_platform', 'Unsupported os-release ID/VERSION_ID')
    return normalized


class Commands:
    def __init__(self, timeout, deadline):
        self.timeout = timeout
        self.deadline = deadline

    def path(self, command):
        for directory in ['/usr/sbin', '/usr/bin', '/sbin', '/bin']:
            candidate = directory + '/' + command
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        raise ScanError('prerequisite_missing', 'Missing executable: ' + command)

    def query(self, arguments):
        if arguments[0] == 'dnf':
            # Native DNF opens logs and cache files during read-only probes.
            with tempfile.TemporaryDirectory(prefix='package-hold-scan-dnf-') as scratch:
                options = ['--setopt={}={}'.format(key, scratch)
                           for key in ('logdir', 'cachedir', 'persistdir')]
                return self._query(arguments[:1] + options + arguments[1:])
        return self._query(arguments)

    def _query(self, arguments):
        path = self.path(arguments[0])
        remaining = min(self.timeout, self.deadline - time.monotonic())
        if remaining <= 0:
            raise ScanError('command_timeout', 'Scan deadline exceeded')
        environment = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root', 'LC_ALL': 'C'}
        try:
            child = subprocess.Popen([path] + arguments[1:], env=environment, shell=False,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     start_new_session=True)
        except OSError as exc:
            raise ScanError('permission_denied' if exc.errno == errno.EACCES else 'command_failed', str(exc)) from exc
        collected = {child.stdout: bytearray(), child.stderr: bytearray()}
        expires = time.monotonic() + remaining
        try:
            with selectors.DefaultSelector() as events:
                for stream in collected:
                    os.set_blocking(stream.fileno(), False)
                    events.register(stream, selectors.EVENT_READ)
                while events.get_map():
                    timeout = expires - time.monotonic()
                    if timeout <= 0:
                        raise ScanError('command_timeout', 'Timed out: ' + arguments[0])
                    ready = events.select(min(timeout, 0.1))
                    for event, _ in ready:
                        block = os.read(event.fileobj.fileno(), 65536)
                        if not block:
                            events.unregister(event.fileobj)
                            continue
                        collected[event.fileobj].extend(block)
                        cap = COMMAND_LIMIT if event.fileobj is child.stdout else 65536
                        if len(collected[event.fileobj]) >= cap:
                            raise ScanError('malformed_output', 'Excessive command output')
            try:
                rc = child.wait(timeout=max(0.001, expires - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                raise ScanError('command_timeout', 'Timed out: ' + arguments[0]) from exc
            output = collected[child.stdout].decode('utf-8', errors='strict')
            diagnostic = collected[child.stderr].decode('utf-8', errors='strict')
            if rc or diagnostic.strip():
                code = 'permission_denied' if 'permission denied' in diagnostic.lower() else 'command_failed'
                raise ScanError(code, '{} rc={}: {}'.format(arguments[0], rc, diagnostic[:2000]))
            return output
        except UnicodeError as exc:
            raise ScanError('malformed_output', 'Command returned invalid UTF-8') from exc
        finally:
            try:
                if child.returncode is None:
                    os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                # Group already exited; reap the leader below.
                pass
            child.wait()
            child.stdout.close()
            child.stderr.close()


def rpm_inventory(output):
    packages = {}
    for row in output.splitlines():
        cells = row.split('\t')
        if len(cells) != 5:
            raise ScanError('malformed_output', 'RPM row must contain five cells')
        name, epoch, version, release, architecture = cells
        # Imported RPM keys are database metadata, not binary packages.
        if (name == 'gpg-pubkey' and epoch == '0' and architecture == '(none)'
                and re.fullmatch(r'[a-f0-9]+', version) and re.fullmatch(r'[a-f0-9]+', release)):
            continue
        if (not re.fullmatch(PACKAGE_RE, name) or not re.fullmatch(r'\d+', epoch, flags=re.ASCII)
                or not re.fullmatch(r'[A-Za-z0-9_+~^.]+', version)
                or not re.fullmatch(r'[A-Za-z0-9_+~^.]+', release)
                or not re.fullmatch(r'[A-Za-z0-9_]+', architecture)):
            raise ScanError('malformed_output', 'Invalid RPM field')
        packages.setdefault(name, []).append(tuple(cells[1:]))
    return packages


def dpkg_inventory(output):
    packages, seen = {}, set()
    for row in output.splitlines():
        cells = row.split('\t')
        if len(cells) != 3:
            raise ScanError('malformed_output', 'dpkg row must contain three cells')
        name, arch, status = cells
        selection = status.split()
        if (not re.fullmatch(r'[a-z0-9][a-z0-9+.\-]+', name)
                or not re.fullmatch(r'[a-z0-9][a-z0-9\-]*', arch) or len(selection) != 3
                or (name, arch) in seen):
            raise ScanError('malformed_output', 'Invalid dpkg name/architecture/status')
        seen.add((name, arch))
        want, error, actual = selection
        if (want not in {'unknown', 'install', 'hold', 'deinstall', 'purge'}
                or error not in {'ok', 'reinstreq'}
                or actual not in {'not-installed', 'config-files', 'installed', 'half-installed',
                                  'unpacked', 'half-configured', 'triggers-awaited', 'triggers-pending'}):
            raise ScanError('malformed_output', 'Unrecognized dpkg state')
        if actual == 'installed':
            state = 'unknown' if error != 'ok' else 'held' if want == 'hold' else 'unheld'
            packages.setdefault(name, []).append((arch, state))
        elif actual not in {'not-installed', 'config-files'} or error != 'ok':
            raise ScanError('command_failed', 'Indeterminate installed state in dpkg DB')
    return packages


def dnf_settings(commands, reader):
    dnf_path = commands.path('dnf')
    main_text = reader('/etc/dnf/dnf.conf')
    plugin_text = reader('/etc/dnf/plugins/versionlock.conf')
    configurations = []
    for text in (main_text, plugin_text):
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        try:
            parser.read_string(text)
        except configparser.Error as exc:
            raise ScanError('configuration_invalid', 'Invalid DNF INI: ' + str(exc)) from exc
        if not parser.has_section('main') or parser.defaults():
            raise ScanError('configuration_invalid', 'Unsupported DNF configuration sections')
        configurations.append(parser)
    main, plugin = configurations
    for parser, key in [(main, 'plugins'), (plugin, 'enabled')]:
        raw = parser.get('main', key, fallback='1').lower()
        if raw not in {'1', '0', 'yes', 'no', 'true', 'false', 'on', 'off'}:
            raise ScanError('configuration_invalid', 'Invalid DNF boolean')
        if raw in {'0', 'no', 'false', 'off'}:
            raise ScanError('backend_disabled', 'DNF plugin disabled')
    for key in ('pluginpath', 'pluginconfpath', 'installroot', 'config_file_path'):
        if main.has_option('main', key):
            raise ScanError('configuration_invalid', 'DNF path overrides unsupported')
    path = plugin.get('main', 'locklist', fallback='')
    if not path.startswith('/') or '\n' in path:
        raise ScanError('configuration_invalid', 'Invalid DNF locklist path')
    version = commands.query(['rpm', '-qf', dnf_path, '--qf', '%{NAME}\t%{VERSION}\n'])
    if not re.fullmatch(r'dnf\t4\.[0-9][A-Za-z0-9._+~^]*\n?', version):
        raise ScanError('unsupported_platform', 'DNF4 required')
    commands.query(['dnf', '-q', '--disablerepo=*', 'versionlock', 'list'])
    rules = reader(path)
    return rules, (main_text, plugin_text, path, rules)


def versionlock_states(rules, installed):
    coverage = {name: set() for name in installed}
    failures = {}
    for line in rules.splitlines():
        record = line.strip()
        if not record or record.startswith('#'):
            continue
        # Split from the right so hyphens in exact names are preserved.
        try:
            left, architecture = record.rsplit('.', 1)
            left, release = left.rsplit('-', 1)
            left, version = left.rsplit(':', 1)
            name, epoch = left.rsplit('-', 1)
        except ValueError:
            name, epoch, version, release, architecture = '', '', '', '', ''
        supported = (re.fullmatch(PACKAGE_RE, name) and re.fullmatch(r'[0-9]+', epoch)
                     and re.fullmatch(r'[A-Za-z0-9_+~^.]+', version)
                     and re.fullmatch(r'[A-Za-z0-9_+~^.]+', release)
                     and re.fullmatch(r'[A-Za-z0-9_]+|\*', architecture))
        if supported:
            if name in coverage:
                coverage[name].add(architecture)
            continue
        code = 'unsupported_rule' if any(c in record for c in '*?[]!:') or re.fullmatch(PACKAGE_RE, record) else 'malformed_output'
        affected = [name for name in installed if record.lstrip('!').startswith(name + '-')]
        prefix = record.lstrip('!').split(':')[0]
        if not affected or any(c in prefix for c in '*?[]'):
            affected = list(installed)
        for name in affected:
            failures[name] = {'code': code, 'message': 'Unsupported versionlock record'}
    result = {}
    for name, builds in installed.items():
        if name in failures:
            result[name] = ('unknown', failures[name])
        else:
            arches = coverage[name]
            held = all('*' in arches or build[3] in arches for build in builds)
            result[name] = ('held' if held else 'unheld', None)
    return result


def alt_pattern(name):
    return '^' + name.replace('+', '[+]').replace('.', '[.]') + '$'


class _GlibcRegex(ctypes.Structure):
    """Public regex_t ABI from glibc regex.h (including the flags bitfield)."""
    _fields_ = [('buffer', ctypes.c_void_p), ('allocated', ctypes.c_ulong),
                ('used', ctypes.c_ulong), ('syntax', ctypes.c_ulong),
                ('fastmap', ctypes.c_void_p), ('translate', ctypes.c_void_p),
                ('re_nsub', ctypes.c_size_t), ('flags', ctypes.c_uint)]


def alt_matches(patterns, names):
    """Use ALT APT's glibc ERE/ICASE/NOSUB flags under the runner's C locale."""
    try:
        libc = ctypes.CDLL(None)
        # Constants and regex_t layout below belong to glibc, not arbitrary libc.
        libc.gnu_get_libc_version
        signatures = {
            'regcomp': ([ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int], ctypes.c_int),
            'regexec': ([ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t,
                         ctypes.c_void_p, ctypes.c_int], ctypes.c_int),
            'regerror': ([ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_size_t], ctypes.c_size_t),
            'regfree': ([ctypes.c_void_p], None),
            'newlocale': ([ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p], ctypes.c_void_p),
            'uselocale': ([ctypes.c_void_p], ctypes.c_void_p),
            'freelocale': ([ctypes.c_void_p], None),
        }
        for symbol, (arguments, result_type) in signatures.items():
            function = getattr(libc, symbol)
            function.argtypes = arguments
            function.restype = result_type
    except (OSError, AttributeError) as exc:
        raise ScanError('unsupported_platform', 'ALT POSIX matcher requires glibc') from exc
    # LC_CTYPE_MASK | LC_COLLATE_MASK; change this thread only, including in HTTP mode.
    c_locale = libc.newlocale(1 | 8, b'C', None)
    if not c_locale:
        raise ScanError('command_failed', 'Cannot create C regex locale')
    previous_locale = libc.uselocale(c_locale)
    if not previous_locale:
        libc.freelocale(c_locale)
        raise ScanError('command_failed', 'Cannot select C regex locale')
    matched = set()
    try:
        for pattern in patterns:
            if '\x00' in pattern:
                raise ScanError('configuration_invalid', 'NUL in RPM::Hold regex')
            expression = _GlibcRegex()
            expression_pointer = ctypes.byref(expression)
            pattern_bytes = pattern.encode('utf-8')
            # REG_EXTENDED | REG_ICASE | REG_NOSUB, as in RPMPackageData.
            compile_status = libc.regcomp(expression_pointer, pattern_bytes, 1 | 2 | 8)
            if compile_status:
                message = ctypes.create_string_buffer(256)
                libc.regerror(compile_status, expression_pointer, message, len(message))
                raise ScanError('configuration_invalid', 'Invalid RPM::Hold regex: ' +
                                message.value.decode('utf-8', errors='replace'))
            try:
                for name in names:
                    name_bytes = name.encode('ascii')
                    match_status = libc.regexec(expression_pointer, name_bytes, 0, None, 0)
                    if match_status == 0:
                        matched.add(name)
                    elif match_status != 1:  # REG_NOMATCH
                        raise ScanError('command_failed', 'RPM::Hold regex execution failed')
            finally:
                libc.regfree(expression_pointer)
    finally:
        libc.uselocale(previous_locale)
        libc.freelocale(c_locale)
    return matched


def alt_rules(output):
    patterns, settings = [], {}
    for line in output.splitlines():
        # ALT dumps values verbatim, including regex backslashes.
        match = re.fullmatch(r'([A-Za-z0-9_:./\-]+) "(.*)";', line)
        if not match:
            raise ScanError('malformed_output', 'Invalid ALT apt-config dump')
        key, decoded = match.groups()
        key = key.lower()
        if key.startswith('rpm::hold::'):
            if '::' in key[len('rpm::hold::'):]:
                raise ScanError('unsupported_rule', 'Nested RPM::Hold entries unsupported')
            patterns.append(decoded)
        elif key == 'rpm::hold':
            if decoded:
                raise ScanError('unsupported_rule', 'Scalar RPM::Hold is unsupported')
        else:
            # APT list keys can legitimately repeat outside RPM::Hold too.
            if key in settings and not key.endswith('::'):
                raise ScanError('configuration_invalid', 'Duplicate ALT scalar setting')
            settings[key] = decoded
    if (settings.get('dir', '/') != '/'
            or settings.get('dir::etc', 'etc/apt/').rstrip('/') not in {'etc/apt', '/etc/apt'}
            or settings.get('dir::etc::parts', 'apt.conf.d/').rstrip('/') not in {'apt.conf.d', '/etc/apt/apt.conf.d'}):
        raise ScanError('configuration_invalid', 'Nonstandard ALT parts directory')
    alt_matches(patterns, ())
    return patterns


def query_packages(config, commands, reader=load_text):
    method = config['backend']
    argv = (['dpkg-query', '-W', '-f=${Package}\t${Architecture}\t${Status}\n']
            if method == 'dpkg_selections' else
            ['rpm', '-qa', '--qf', '%{NAME}\t%{EPOCHNUM}\t%{VERSION}\t%{RELEASE}\t%{ARCH}\n'])
    parse_inventory = dpkg_inventory if method == 'dpkg_selections' else rpm_inventory
    for attempt in range(2):
        installed_output = commands.query(argv)
        inventory = parse_inventory(installed_output)
        selected = {item['name']: inventory[item['name']] for item in config['packages'] if item['name'] in inventory}
        if not selected:
            return []
        state_snapshot = None
        try:
            if method == 'dpkg_selections':
                commands.path('apt-mark')
                states = {}
                for name, architectures in selected.items():
                    statuses = [entry[1] for entry in architectures]
                    if 'unknown' in statuses:
                        states[name] = ('unknown', {'code': 'command_failed', 'message': 'dpkg reinstreq'})
                    else:
                        states[name] = ('held' if all(s == 'held' for s in statuses) else 'unheld', None)
            elif method == 'dnf_versionlock':
                rules, state_snapshot = dnf_settings(commands, reader)
                states = versionlock_states(rules, selected)
            else:
                commands.path('apt-get')
                state_snapshot = commands.query(['apt-config', 'dump'])
                patterns = alt_rules(state_snapshot)
                matched = alt_matches(patterns, selected)
                states = {name: ('held' if name in matched else 'unheld', None) for name in selected}
        except ScanError as exc:
            states = {name: ('unknown', exc.detail) for name in selected}
        if method != 'dpkg_selections':
            confirmed = commands.query(argv)
            unchanged = installed_output == confirmed
            if method == 'dnf_versionlock' and state_snapshot is not None:
                main, plugin, path, rules = state_snapshot
                unchanged = unchanged and (reader('/etc/dnf/dnf.conf') == main
                                             and reader('/etc/dnf/plugins/versionlock.conf') == plugin
                                             and reader(path) == rules)
            elif method == 'alt_rpm_hold' and state_snapshot is not None:
                unchanged = unchanged and commands.query(['apt-config', 'dump']) == state_snapshot
            if not unchanged:
                if attempt == 0:
                    continue
                raise ScanError('concurrent_change', 'Package DB/config changed during scan')
        return [{'id': item['id'], 'package': item['name'], 'method': method,
                 'state': states[item['name']][0], 'error': states[item['name']][1]}
                for item in config['packages'] if item['name'] in selected]
    raise ScanError('concurrent_change', 'Unstable package state')


def atomic_json(path, payload):
    encoded = (json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(',', ':')) + '\n').encode('utf-8')
    if len(encoded) > CACHE_LIMIT:
        raise ScanError('cache_io_error', 'Generated cache exceeds limit')
    directory = os.path.dirname(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.status-', dir=directory)
    try:
        with os.fdopen(descriptor, 'wb') as target:
            os.fchmod(target.fileno(), 0o644)
            target.write(encoded)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def valid_error(value):
    return (isinstance(value, dict) and set(value) == {'code', 'message'}
            and all(isinstance(value[k], str) and value[k] for k in value))


def validate_cache(cache, config, identity):
    keys = {'schema_version', 'scan_id', 'boot_id', 'config_digest', 'last_attempt_at',
            'last_completed_at', 'last_success_at', 'scan_success', 'scan_duration_seconds',
            'packages', 'errors'}
    if not isinstance(cache, dict) or set(cache) != keys:
        raise ValueError('Invalid cache keys')
    if (type(cache['schema_version']) is not int or cache['schema_version'] != 1
            or cache['config_digest'] != config_digest(config) or cache['boot_id'] != identity
            or type(cache['scan_success']) is not bool):
        raise ValueError('Cache schema/identity mismatch')
    if not isinstance(cache['scan_id'], str) or not isinstance(cache['boot_id'], str):
        raise ValueError('Invalid cache UUID type')
    uuid.UUID(cache['scan_id'])
    uuid.UUID(cache['boot_id'])
    for key in ('last_attempt_at', 'last_completed_at', 'last_success_at', 'scan_duration_seconds'):
        value = cache[key]
        if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
            raise ValueError('Invalid cache timestamp/duration')
    attempted, completed, successful = (cache['last_attempt_at'], cache['last_completed_at'], cache['last_success_at'])
    if (attempted <= 0 or (completed and completed < attempted)
            or (cache['scan_success'] and (not completed or successful != completed))):
        raise ValueError('Inconsistent scan timestamps')
    if not isinstance(cache['errors'], list) or not all(valid_error(e) for e in cache['errors']):
        raise ValueError('Invalid cache errors')
    if not isinstance(cache['packages'], list) or len(cache['packages']) > len(config['packages']):
        raise ValueError('Invalid cache packages')
    resolved = {item['id']: item['name'] for item in config['packages']}
    seen = set()
    for row in cache['packages']:
        if not isinstance(row, dict) or set(row) != {'id', 'package', 'method', 'state', 'error',
                                                      'unheld_streak', 'unheld_since', 'persistent_unheld'}:
            raise ValueError('Invalid cache row')
        if (not isinstance(row['id'], str) or row['id'] in seen or row['id'] not in resolved
                or row['package'] != resolved[row['id']] or row['method'] != config['backend']
                or row['state'] not in ('held', 'unheld', 'unknown')):
            raise ValueError('Unexpected cache package')
        seen.add(row['id'])
        if (row['state'] == 'unknown' and not valid_error(row['error'])) or (row['state'] != 'unknown' and row['error'] is not None):
            raise ValueError('Inconsistent package error')
        streak, since, persistent = row['unheld_streak'], row['unheld_since'], row['persistent_unheld']
        if (type(streak) is not int or streak < 0 or type(since) not in (int, float)
                or not math.isfinite(since) or since < 0 or type(persistent) is not bool):
            raise ValueError('Invalid unheld evidence')
        if row['state'] == 'unheld':
            expected = streak >= 3 and completed - since > config['scan_interval_seconds']
            if streak < 1 or since <= 0 or since > completed or persistent != expected:
                raise ValueError('Inconsistent unheld evidence')
        elif streak != 0 or since != 0 or persistent:
            raise ValueError('Evidence on non-unheld package')
    errors = cache['errors'] or any(row['error'] for row in cache['packages'])
    if cache['scan_success'] and errors:
        raise ValueError('Successful scan has errors')
    if not completed and (cache['packages'] or cache['scan_success']):
        raise ValueError('Incomplete scan has results')
    if cache['errors'] and cache['packages']:
        raise ValueError('Host inventory failure has package results')
    return cache


def read_cache(config, identity=None):
    try:
        content = load_text(config['status_path'], CACHE_LIMIT)
        cache = parse_json(content)
        return validate_cache(cache, config, identity or boot_id())
    except (ScanError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        LOG.debug('Cache unavailable or invalid: %s', exc)
        return None


def stale_after(config):
    return 2 * config['scan_interval_seconds'] + config['scan_timeout_seconds'] + 2


def cache_fresh(cache, config, now):
    return bool(cache and cache['last_completed_at'] > 0
                and 0 <= now - cache['last_completed_at'] <= stale_after(config))


def finish_observations(rows, previous, config, completed):
    previous_rows = {}
    if cache_fresh(previous, config, completed):
        gap = completed - previous['last_completed_at']
        if 0 < gap <= config['scan_interval_seconds'] + config['scan_timeout_seconds'] + 2:
            previous_rows = {row['id']: row for row in previous['packages']}
    for row in rows:
        old = previous_rows.get(row['id'])
        streak, since = 0, 0
        if row['state'] == 'unheld':
            if old and old['package'] == row['package'] and old['state'] == 'unheld':
                streak, since = old['unheld_streak'] + 1, old['unheld_since']
            else:
                streak, since = 1, completed
        row.update(unheld_streak=streak, unheld_since=since,
                   persistent_unheld=streak >= 3 and completed - since > config['scan_interval_seconds'])
    return rows


def scan(config, commands=None, reader=load_text, clock=time.time,
         identity=None, lock_path='/run/package-hold-exporter/scan.lock'):
    check_config(config)
    identity = identity or boot_id()
    start_monotonic = time.monotonic()
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            LOG.error('Another scan is running')
            return 3
        previous = read_cache(config, identity)
        attempted = clock()
        marker = {'schema_version': 1, 'scan_id': str(uuid.uuid4()), 'boot_id': identity,
                  'config_digest': config_digest(config), 'last_attempt_at': attempted,
                  'last_completed_at': 0, 'last_success_at': previous['last_success_at'] if previous else 0,
                  'scan_success': False, 'scan_duration_seconds': 0, 'packages': [], 'errors': []}
        # A complete marker replaces the old cache atomically. A killed attempt
        # cannot present the old package observations as a fresh successful scan.
        atomic_json(config['status_path'], marker)
        commands = commands or Commands(config['command_timeout_seconds'],
                                         start_monotonic + config['scan_timeout_seconds'])
        errors, rows = [], []
        try:
            if current_os(reader) != config['os']:
                raise ScanError('unsupported_platform', 'Config OS differs from this host')
            rows = query_packages(config, commands, reader)
        except ScanError as exc:
            errors.append(exc.detail)
            LOG.error('Scan failed: %s', exc)
        completed = clock()
        if completed < attempted or time.monotonic() - start_monotonic > config['scan_timeout_seconds']:
            rows = []
            errors = [{'code': 'command_timeout', 'message': 'Scan deadline or wall clock changed'}]
            completed = max(attempted, completed)
        success = not errors and not any(row['error'] for row in rows)
        final = dict(marker, last_completed_at=completed, scan_success=success,
                     last_success_at=completed if success else marker['last_success_at'],
                     scan_duration_seconds=time.monotonic() - start_monotonic,
                     packages=finish_observations(rows, previous, config, completed), errors=errors)
        validate_cache(final, config, identity)
        atomic_json(config['status_path'], final)
        return 0 if success else 1
    finally:
        os.close(descriptor)


def label_escape(value):
    return value.replace('\\', '\\\\').replace('\n', '\\n').replace('"', '\\"')


def metrics(config, cache, now=None):
    now = time.time() if now is None else now
    fresh = cache_fresh(cache, config, now)
    output = []
    definitions = [
        ('package_hold_status', 'Installed package hold: 1 held, 0 unheld; unknown omitted.'),
        ('package_hold_check_success', 'Installed package hold check succeeded.'),
        ('package_hold_persistent_unheld', 'Three consecutive unheld scans spanning more than one interval.'),
        ('package_hold_cache_valid', 'Cache schema and identity valid.'),
        ('package_hold_scan_success', 'Last scan completed without errors.'),
        ('package_hold_scan_last_attempt_timestamp_seconds', 'Last scan attempt UNIX timestamp.'),
        ('package_hold_scan_last_completed_timestamp_seconds', 'Last completed scan UNIX timestamp.'),
        ('package_hold_scan_last_success_timestamp_seconds', 'Last fully successful scan UNIX timestamp.'),
        ('package_hold_scan_duration_seconds', 'Duration of the last scan.'),
        ('package_hold_scan_interval_seconds', 'Configured scan interval.'),
        ('package_hold_scan_stale_after_seconds', 'Maximum accepted observation age.'),
    ]
    for name, description in definitions:
        output.extend(['# HELP ' + name + ' ' + description, '# TYPE ' + name + ' gauge'])
    health = {
        'cache_valid': int(cache is not None),
        'scan_success': int(bool(cache and cache['scan_success'])),
        'scan_last_attempt_timestamp_seconds': cache['last_attempt_at'] if cache else 0,
        'scan_last_completed_timestamp_seconds': cache['last_completed_at'] if cache else 0,
        'scan_last_success_timestamp_seconds': cache['last_success_at'] if cache else 0,
        'scan_duration_seconds': cache['scan_duration_seconds'] if cache else 0,
        'scan_interval_seconds': config['scan_interval_seconds'],
        'scan_stale_after_seconds': stale_after(config),
    }
    output.extend('package_hold_{} {}'.format(key, value) for key, value in health.items())
    if fresh:
        for row in cache['packages']:
            labels = '{{package_id="{}",package="{}",method="{}"}}'.format(
                label_escape(row['id']), label_escape(row['package']), label_escape(row['method']))
            output.append('package_hold_check_success{} {}'.format(labels, int(row['state'] != 'unknown')))
            if row['state'] != 'unknown':
                output.append('package_hold_status{} {}'.format(labels, int(row['state'] == 'held')))
            output.append('package_hold_persistent_unheld{} {}'.format(labels, int(row['persistent_unheld'])))
    return '\n'.join(output) + '\n'


def snapshot(config, cache, now=None):
    fresh = cache_fresh(cache, config, time.time() if now is None else now)
    result = {'cache_valid': int(cache is not None), 'fresh': int(fresh),
              'discovery_valid': int(bool(fresh and not cache['errors'])),
              'scan_success': int(bool(cache and cache['scan_success'])),
              'last_attempt': cache['last_attempt_at'] if cache else 0,
              'last_completed': cache['last_completed_at'] if cache else 0,
              'last_success': cache['last_success_at'] if cache else 0,
              'duration': cache['scan_duration_seconds'] if cache else 0,
              'interval': config['scan_interval_seconds'], 'stale_after': stale_after(config), 'packages': []}
    if fresh:
        for row in cache['packages']:
            result['packages'].append({'id': row['id'], 'package': row['package'], 'method': row['method'],
                                       'status': None if row['state'] == 'unknown' else int(row['state'] == 'held'),
                                       'check_success': int(row['state'] != 'unknown'),
                                       'persistent': int(row['persistent_unheld'])})
    return result


class CacheHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    max_clients = 16

    def __init__(self, address, config):
        self.config = config
        self.client_slots = threading.BoundedSemaphore(self.max_clients)
        if ':' in address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(address, CacheHandler)

    def process_request(self, request, client_address):
        if not self.client_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.client_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.client_slots.release()

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address


class CacheHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/metrics':
            # Only regular bounded cache/config data; no Commands or OS detection.
            cached = read_cache(self.server.config)
            body = metrics(self.server.config, cached).encode('utf-8')
            status, content_type = 200, 'text/plain; version=0.0.4; charset=utf-8'
        elif self.path == '/healthz':
            body, status, content_type = b'ok\n', 200, 'text/plain; charset=utf-8'
        else:
            body, status, content_type = b'not found\n', 404, 'text/plain; charset=utf-8'
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, TimeoutError) as exc:
            LOG.debug('HTTP client disconnected: %s', exc)

    def log_message(self, message, *arguments):
        LOG.debug(message, *arguments)


def serve(config):
    server = CacheHTTPServer((config['listen_address'], config['listen_port']), config)
    stopping = threading.Event()

    def stop(signum, frame):
        if not stopping.is_set():
            stopping.set()
            threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest='command', required=True)
    for command in ('scan', 'serve', 'zabbix-snapshot', 'zabbix-discovery', 'zabbix-get'):
        child = subcommands.add_parser(command)
        child.add_argument('--config', default='/etc/package-hold-exporter/config.json')
        if command == 'zabbix-get':
            child.add_argument('--package-id')
            child.add_argument('--field', required=True, choices=['status', 'check_success', 'persistent',
                                'cache_valid', 'fresh', 'scan_success', 'last_attempt', 'last_completed',
                                'last_success', 'duration', 'interval', 'stale_after'])
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    try:
        try:
            config = check_config(parse_json(load_text(arguments.config, CACHE_LIMIT)))
        except (ScanError, ValueError, RecursionError) as exc:
            LOG.error('Invalid config: %s', exc)
            return 2
        if arguments.command == 'scan':
            return scan(config)
        if arguments.command == 'serve':
            serve(config)
            return 0
        data = snapshot(config, read_cache(config))
        if arguments.command == 'zabbix-snapshot':
            print(json.dumps(data, allow_nan=False))
        elif arguments.command == 'zabbix-discovery':
            discoveries = [{'{#PACKAGE_ID}': row['id'], '{#PACKAGE}': row['package'], '{#METHOD}': row['method']}
                           for row in data['packages']]
            print(json.dumps(discoveries))
        else:
            if arguments.package_id:
                row = next((row for row in data['packages'] if row['id'] == arguments.package_id), None)
                if row is None or arguments.field not in row or row[arguments.field] is None:
                    raise ScanError('command_failed', 'Package observation unavailable')
                value = row[arguments.field]
            elif arguments.field in data and arguments.field != 'packages':
                value = data[arguments.field]
            else:
                raise ScanError('configuration_invalid', 'Package field requires --package-id')
            print(value)
        return 0
    except (ScanError, OSError, ValueError) as exc:
        LOG.error('%s', exc)
        configuration_error = isinstance(exc, ValueError) or (isinstance(exc, ScanError) and exc.detail['code'] == 'configuration_invalid')
        return 2 if configuration_error else 1


if __name__ == '__main__':
    raise SystemExit(main())
