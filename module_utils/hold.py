"""Native hold adapters for Ansible. Exporter has an independent implementation."""
import configparser
import ctypes
import errno
import fcntl
import os
import re
import selectors
import shlex
import signal
import stat
import subprocess
import tempfile
import time
from contextlib import contextmanager

BACKENDS = {'redos': 'dnf_versionlock', 'astra': 'dpkg_selections', 'alt': 'alt_rpm_hold'}
NAME = r'[A-Za-z0-9][A-Za-z0-9_.+\-]{0,254}'
RPM_FORMAT = '%{NAME}\t%{EPOCHNUM}\t%{VERSION}\t%{RELEASE}\t%{ARCH}\n'
DPKG_FORMAT = '${Package}\t${Architecture}\t${Status}\n'
MAX_OUTPUT = 16 * 1024 * 1024
ALT_FRAGMENT = '/etc/apt/apt.conf.d/90-package-hold.conf'


class HoldError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.error = {'code': code, 'message': message}


def read_text(path, limit=MAX_OUTPUT):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise HoldError('configuration_invalid', 'Regular file required: ' + path)
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise HoldError('malformed_output', 'File exceeds size limit: ' + path)
        return data.decode('utf-8', errors='strict')
    except PermissionError as exc:
        raise HoldError('permission_denied', str(exc)) from exc
    except UnicodeError as exc:
        raise HoldError('malformed_output', str(exc)) from exc
    except OSError as exc:
        raise HoldError('prerequisite_missing', str(exc)) from exc


def detect_os(reader=read_text):
    fields = {}
    for line in reader('/etc/os-release').splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        key, separator, raw = line.partition('=')
        if not separator or key in fields:
            raise HoldError('configuration_invalid', 'Invalid os-release')
        try:
            values = shlex.split(raw, comments=False, posix=True)
        except ValueError as exc:
            raise HoldError('configuration_invalid', str(exc)) from exc
        fields[key] = values[0] if len(values) == 1 else ''
    os_id = {'redos': 'redos', 'astra': 'astra', 'altlinux': 'alt'}.get(fields.get('ID'))
    version = fields.get('VERSION_ID', '')
    if os_id is None or not version:
        raise HoldError('unsupported_platform', 'Supported os-release ID and VERSION_ID required')
    return {'id': os_id, 'version_id': version}


def validate_packages(packages, backend):
    if not isinstance(packages, list) or len(packages) > 1024:
        raise HoldError('configuration_invalid', 'packages must be a list, at most 1024 entries')
    ids, names = set(), set()
    for item in packages:
        if not isinstance(item, dict) or set(item) != {'id', 'name'}:
            raise HoldError('configuration_invalid', 'Each package requires only id and name')
        package_id, name = item['id'], item['name']
        if not isinstance(package_id, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', package_id):
            raise HoldError('configuration_invalid', 'Invalid package id')
        if not isinstance(name, str) or not re.fullmatch(NAME, name):
            raise HoldError('configuration_invalid', 'Invalid exact package name')
        if backend == 'dpkg_selections' and not re.fullmatch(r'[a-z0-9][a-z0-9+.\-]+', name):
            raise HoldError('configuration_invalid', 'Invalid dpkg package name')
        if package_id in ids or name in names:
            raise HoldError('configuration_invalid', 'Duplicate package id or name')
        ids.add(package_id)
        names.add(name)


class Runner:
    """Bound output while draining both pipes; kill and reap the process group."""
    def __init__(self, timeout=30, deadline=None):
        self.timeout = timeout
        self.deadline = deadline

    def executable(self, name):
        for directory in ('/usr/sbin', '/usr/bin', '/sbin', '/bin'):
            path = os.path.join(directory, name)
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        raise HoldError('prerequisite_missing', 'Executable missing: ' + name)

    def run(self, argv, query=True):
        if query and argv[0] == 'dnf':
            # DNF initializes logs/cache even for `versionlock list`. Keep audit
            # writes disposable and compatible with ProtectSystem=strict.
            with tempfile.TemporaryDirectory(prefix='package-hold-dnf-') as directory:
                options = ['--setopt=' + key + '=' + directory
                           for key in ('logdir', 'cachedir', 'persistdir')]
                return self._run(argv[:1] + options + argv[1:], query)
        return self._run(argv, query)

    def _run(self, argv, query):
        executable = self.executable(argv[0])
        timeout = self.timeout
        if self.deadline is not None:
            timeout = min(timeout, self.deadline - time.monotonic())
        if timeout <= 0:
            raise HoldError('command_timeout', 'Operation deadline exceeded')
        environment = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C', 'HOME': '/root'}
        try:
            process = subprocess.Popen([executable] + argv[1:], stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, env=environment,
                                       shell=False, start_new_session=True)
        except OSError as exc:
            code = 'permission_denied' if exc.errno == errno.EACCES else 'command_failed'
            raise HoldError(code, str(exc)) from exc
        buffers = {process.stdout: bytearray(), process.stderr: bytearray()}
        end = time.monotonic() + timeout
        try:
            with selectors.DefaultSelector() as selector:
                for pipe in buffers:
                    os.set_blocking(pipe.fileno(), False)
                    selector.register(pipe, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = end - time.monotonic()
                    if remaining <= 0:
                        raise HoldError('command_timeout', 'Command timed out: ' + argv[0])
                    for key, _ in selector.select(min(remaining, 0.1)):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        buffer = buffers[key.fileobj]
                        buffer.extend(chunk)
                        limit = MAX_OUTPUT if key.fileobj is process.stdout else 65536
                        if len(buffer) >= limit:
                            raise HoldError('malformed_output', 'Command output limit exceeded')
            try:
                returncode = process.wait(timeout=max(0.001, end - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                raise HoldError('command_timeout', 'Command timed out: ' + argv[0]) from exc
            stdout = buffers[process.stdout].decode('utf-8', errors='strict')
            stderr = buffers[process.stderr].decode('utf-8', errors='strict')
            if returncode != 0 or (query and stderr.strip()):
                code = 'permission_denied' if 'permission denied' in stderr.lower() else 'command_failed'
                raise HoldError(code, '{} rc={}: {}'.format(argv[0], returncode, stderr[:2000]))
            return stdout
        except UnicodeError as exc:
            raise HoldError('malformed_output', 'Invalid command UTF-8') from exc
        finally:
            # Descendants retaining pipes also need to be stopped on timeout.
            try:
                if process.returncode is None:
                    os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                # All members have exited; nothing remains to reap except the leader.
                pass
            process.wait()
            process.stdout.close()
            process.stderr.close()


def parse_rpm(output):
    inventory = {}
    for line in output.splitlines():
        fields = line.split('\t')
        if len(fields) != 5:
            raise HoldError('malformed_output', 'RPM inventory requires five fields')
        name, epoch, version, release, arch = fields
        # RPM stores imported signing keys as metadata pseudo-packages.
        if (name == 'gpg-pubkey' and epoch == '0' and arch == '(none)'
                and re.fullmatch(r'[a-f0-9]+', version) and re.fullmatch(r'[a-f0-9]+', release)):
            continue
        if (not re.fullmatch(NAME, name) or not re.fullmatch(r'[0-9]+', epoch)
                or not all(re.fullmatch(r'[A-Za-z0-9_+~^.]+', token) for token in (version, release))
                or not re.fullmatch(r'[A-Za-z0-9_]+', arch)):
            raise HoldError('malformed_output', 'Invalid RPM inventory token')
        inventory.setdefault(name, []).append({'arch': arch, 'nevra': '{}-{}:{}-{}.{}'.format(*fields)})
    return inventory


def parse_dpkg(output):
    inventory = {}
    seen = set()
    for line in output.splitlines():
        fields = line.split('\t')
        if len(fields) != 3:
            raise HoldError('malformed_output', 'dpkg inventory requires three fields')
        name, arch, status = fields
        tokens = status.split()
        if (not re.fullmatch(r'[a-z0-9][a-z0-9+.\-]+', name)
                or not re.fullmatch(r'[a-z0-9][a-z0-9\-]*', arch) or len(tokens) != 3
                or (name, arch) in seen):
            raise HoldError('malformed_output', 'Invalid or duplicate dpkg row')
        seen.add((name, arch))
        want, error, actual = tokens
        if (want not in ('hold', 'install', 'deinstall', 'purge', 'unknown')
                or error not in ('ok', 'reinstreq')
                or actual not in ('installed', 'config-files', 'not-installed', 'half-installed',
                                  'unpacked', 'half-configured', 'triggers-awaited', 'triggers-pending')):
            raise HoldError('malformed_output', 'Unknown dpkg status token')
        if actual == 'installed':
            state = 'unknown' if error != 'ok' else ('held' if want == 'hold' else 'unheld')
            inventory.setdefault(name, []).append({'arch': arch, 'state': state})
        elif actual not in ('config-files', 'not-installed') or error != 'ok':
            raise HoldError('command_failed', 'dpkg inventory has an indeterminate installed state')
    return inventory


def ini(text):
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        raise HoldError('configuration_invalid', str(exc)) from exc
    if parser.defaults():
        raise HoldError('configuration_invalid', 'INI DEFAULT overrides unsupported')
    return parser


def enabled(parser, option, default=True):
    if not parser.has_section('main'):
        raise HoldError('configuration_invalid', 'Missing main section')
    raw = parser.get('main', option, fallback='1' if default else '0').lower()
    if raw not in ('1', '0', 'yes', 'no', 'true', 'false', 'on', 'off'):
        raise HoldError('configuration_invalid', 'Invalid boolean: ' + option)
    if raw in ('0', 'no', 'false', 'off'):
        raise HoldError('backend_disabled', 'Backend disabled: ' + option)


def canonical_pattern(name):
    return '^' + name.replace('.', '[.]').replace('+', '[+]') + '$'


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
        raise HoldError('unsupported_platform', 'ALT POSIX matcher requires glibc') from exc
    # LC_CTYPE_MASK | LC_COLLATE_MASK; change this thread only, including in HTTP mode.
    c_locale = libc.newlocale(1 | 8, b'C', None)
    if not c_locale:
        raise HoldError('command_failed', 'Cannot create C regex locale')
    previous_locale = libc.uselocale(c_locale)
    if not previous_locale:
        libc.freelocale(c_locale)
        raise HoldError('command_failed', 'Cannot select C regex locale')
    matched = set()
    try:
        for pattern in patterns:
            if '\x00' in pattern:
                raise HoldError('configuration_invalid', 'NUL in RPM::Hold regex')
            expression = _GlibcRegex()
            expression_pointer = ctypes.byref(expression)
            pattern_bytes = pattern.encode('utf-8')
            # REG_EXTENDED | REG_ICASE | REG_NOSUB, as in RPMPackageData.
            compile_status = libc.regcomp(expression_pointer, pattern_bytes, 1 | 2 | 8)
            if compile_status:
                message = ctypes.create_string_buffer(256)
                libc.regerror(compile_status, expression_pointer, message, len(message))
                raise HoldError('configuration_invalid', 'Invalid RPM::Hold regex: ' +
                                message.value.decode('utf-8', errors='replace'))
            try:
                for name in names:
                    name_bytes = name.encode('ascii')
                    match_status = libc.regexec(expression_pointer, name_bytes, 0, None, 0)
                    if match_status == 0:
                        matched.add(name)
                    elif match_status != 1:  # REG_NOMATCH
                        raise HoldError('command_failed', 'RPM::Hold regex execution failed')
            finally:
                libc.regfree(expression_pointer)
    finally:
        libc.uselocale(previous_locale)
        libc.freelocale(c_locale)
    return matched


def parse_alt(output):
    patterns = []
    settings = {}
    for line in output.splitlines():
        # ALT dumps values verbatim, including regex backslashes.
        match = re.fullmatch(r'([A-Za-z0-9_:./\-]+) "(.*)";', line)
        if not match:
            raise HoldError('malformed_output', 'Invalid apt-config dump line')
        key, value = match.groups()
        key = key.lower()
        if key.startswith('rpm::hold::'):
            if '::' in key[len('rpm::hold::'):]:
                raise HoldError('unsupported_rule', 'Nested RPM::Hold entries unsupported')
            patterns.append(value)
        elif key == 'rpm::hold':
            if value:
                raise HoldError('unsupported_rule', 'Scalar RPM::Hold unsupported')
        else:
            if key in settings and not key.endswith('::'):
                raise HoldError('configuration_invalid', 'Duplicate scalar apt config key')
            settings[key] = value
    etc = settings.get('dir::etc', 'etc/apt/')
    parts = settings.get('dir::etc::parts', 'apt.conf.d/')
    if (settings.get('dir', '/') != '/' or etc.rstrip('/') not in ('etc/apt', '/etc/apt')
            or parts.rstrip('/') not in ('apt.conf.d', '/etc/apt/apt.conf.d')):
        raise HoldError('configuration_invalid', 'Nonstandard ALT config directory')
    alt_matches(patterns, ())
    return patterns


def exact_lock(rule):
    return re.fullmatch(r'(' + NAME + r')-([0-9]+):([A-Za-z0-9_+~^.]+)-([A-Za-z0-9_+~^.]+)\.([A-Za-z0-9_]+|\*)', rule)


def parse_locks(output, inventory):
    held = {name: set() for name in inventory}
    errors = {}
    for line in output.splitlines():
        rule = line.strip()
        if not rule or rule.startswith('#'):
            continue
        match = exact_lock(rule)
        if match:
            name, _, _, _, arch = match.groups()
            if name in held:
                held[name].add(arch)
            continue
        code = 'unsupported_rule' if any(char in rule for char in '*?[]!:') or re.fullmatch(NAME, rule) else 'malformed_output'
        # Exact prefix lets unrelated unsupported records stay unrelated. Broad names
        # have uncertain scope and invalidate every monitored package conservatively.
        affected = [name for name in inventory if rule.lstrip('!').startswith(name + '-')]
        name_part = rule.lstrip('!').split(':')[0]
        if not affected or any(char in name_part for char in '*?[]'):
            affected = list(inventory)
        for name in affected:
            errors[name] = {'code': code, 'message': 'Unsupported versionlock record'}
    states = {}
    for name, builds in inventory.items():
        if name in errors:
            states[name] = ('unknown', errors[name])
        else:
            covered = held[name]
            locked = all('*' in covered or build['arch'] in covered for build in builds)
            states[name] = ('held' if locked else 'unheld', None)
    return states


class Backend:
    def __init__(self, method, runner=None, reader=read_text):
        self.method = method
        self.runner = runner or Runner()
        self.reader = reader

    def installed(self):
        if self.method == 'dpkg_selections':
            output = self.runner.run(['dpkg-query', '-W', '-f=' + DPKG_FORMAT])
            return parse_dpkg(output)
        output = self.runner.run(['rpm', '-qa', '--qf', RPM_FORMAT])
        return parse_rpm(output)

    def dnf_config(self):
        dnf_path = self.runner.executable('dnf')
        main_text = self.reader('/etc/dnf/dnf.conf')
        plugin_text = self.reader('/etc/dnf/plugins/versionlock.conf')
        global_config = ini(main_text)
        enabled(global_config, 'plugins')
        for key in ('pluginpath', 'pluginconfpath', 'installroot', 'config_file_path'):
            if global_config.has_option('main', key):
                raise HoldError('configuration_invalid', 'Nondefault DNF override: ' + key)
        plugin = ini(plugin_text)
        enabled(plugin, 'enabled')
        locklist = plugin.get('main', 'locklist', fallback='')
        if not locklist.startswith('/') or '\n' in locklist:
            raise HoldError('configuration_invalid', 'Absolute DNF locklist required')
        # A configured but uninstalled/unsupported plugin is not an active hold.
        # `dnf --version` opens hawkey.log before applying logdir overrides.
        # Query the RPM owner of the actual executable without starting DNF.
        version = self.runner.run(['rpm', '-qf', dnf_path, '--qf', '%{NAME}\t%{VERSION}\n'])
        if not re.fullmatch(r'dnf\t4\.[0-9][A-Za-z0-9._+~^]*\n?', version):
            raise HoldError('unsupported_platform', 'DNF4 required')
        self.runner.run(['dnf', '-q', '--disablerepo=*', 'versionlock', 'list'])
        self._dnf_lock_path = locklist
        self._dnf_locks = self.reader(locklist)
        # Publish a complete snapshot only after all probes and reads succeed.
        self._dnf_text = (main_text, plugin_text)
        return self._dnf_locks

    def states(self, inventory):
        if not inventory:
            return {}
        try:
            if self.method == 'dnf_versionlock':
                locks = self.dnf_config()
                return parse_locks(locks, inventory)
            if self.method == 'alt_rpm_hold':
                self.runner.executable('apt-get')
                output = self.runner.run(['apt-config', 'dump'])
                self._alt_dump = output
                patterns = parse_alt(output)
                matched = alt_matches(patterns, inventory)
                return {name: ('held' if name in matched else 'unheld', None)
                        for name in inventory}
            self.runner.executable('apt-mark')
            states = {}
            for name, arches in inventory.items():
                values = [arch['state'] for arch in arches]
                if 'unknown' in values:
                    states[name] = ('unknown', {'code': 'command_failed', 'message': 'dpkg reinstreq'})
                else:
                    states[name] = ('held' if all(value == 'held' for value in values) else 'unheld', None)
            return states
        except HoldError as exc:
            return {name: ('unknown', exc.error) for name in inventory}

    def query(self, packages):
        for attempt in range(2):
            inventory = self.installed()
            monitored = {item['name']: inventory[item['name']] for item in packages if item['name'] in inventory}
            self._dnf_text, self._alt_dump = None, None
            states = self.states(monitored)
            if monitored and self.method != 'dpkg_selections':
                confirmed = self.installed()
                unchanged = confirmed == inventory
                if self._dnf_text is not None:
                    unchanged = unchanged and self._dnf_text == (
                        self.reader('/etc/dnf/dnf.conf'), self.reader('/etc/dnf/plugins/versionlock.conf'))
                    unchanged = unchanged and self.reader(self._dnf_lock_path) == self._dnf_locks
                if self._alt_dump is not None:
                    unchanged = unchanged and self.runner.run(['apt-config', 'dump']) == self._alt_dump
                if not unchanged:
                    if attempt == 0:
                        continue
                    raise HoldError('concurrent_change', 'Package DB/config changed during query')
            rows = []
            for item in packages:
                if item['name'] in monitored:
                    state, error = states[item['name']]
                    rows.append({'id': item['id'], 'package': item['name'], 'state': state, 'error': error})
            return rows, monitored
        raise HoldError('concurrent_change', 'Unstable package state')

    def needs_change(self, row, builds, desired):
        if row['state'] == 'unknown':
            return False
        if desired == 'unheld' and self.method == 'dpkg_selections':
            return any(build['state'] == 'held' for build in builds)
        if desired == 'unheld' and self.method == 'dnf_versionlock':
            return bool(self.lock_records(row['package']))
        return row['state'] != desired

    def lock_records(self, name):
        records = []
        for line in self._dnf_locks.splitlines():
            rule = line.strip()
            match = exact_lock(rule)
            if match and match.group(1) == name:
                records.append(rule)
        return records

    def mutate(self, name, builds, desired):
        if self.method == 'dpkg_selections':
            operation = 'hold' if desired == 'held' else 'unhold'
            targets = [name + ':' + build['arch'] for build in builds]
            self.runner.run(['apt-mark', operation] + targets, query=False)
        elif self.method == 'dnf_versionlock':
            operation = 'add' if desired == 'held' else 'delete'
            if desired == 'held':
                targets = [build['nevra'] for build in builds]
            else:
                # DNF matches NAME, NAME-VERSION and other aliases. A bare
                # "tool-2" can delete the lock for tool version 2 as well.
                # Escape the stored arch wildcard to select literal records.
                targets = [record.replace('*', '[*]') for record in self.lock_records(name)]
                if not targets:
                    return False
            self.runner.run(['dnf', '-q', '--disablerepo=*', 'versionlock', operation] + targets, query=False)
        else:
            return change_alt_fragment(name, desired, self.reader)
        return True


def atomic_write(path, data, mode=0o644):
    directory = os.path.dirname(path)
    fd, temporary = tempfile.mkstemp(prefix='.package-hold-', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def change_alt_fragment(name, desired, reader=read_text):
    path = ALT_FRAGMENT
    if os.path.islink(path):
        raise HoldError('configuration_invalid', 'Owned ALT fragment must not be a symlink')
    entries = set()
    original = None
    if os.path.exists(path):
        content = reader(path)
        original = content
        match = re.fullmatch(r'RPM::Hold\s*\{(.*?)\};\s*', content, re.DOTALL)
        if not match:
            raise HoldError('configuration_invalid', 'Unrecognized owned ALT fragment')
        body = match.group(1)
        for line in body.splitlines():
            if not line.strip():
                continue
            pattern_match = re.fullmatch(r'\s*"([^"]*)";\s*', line)
            if not pattern_match:
                raise HoldError('configuration_invalid', 'Unrecognized owned ALT entry')
            pattern = pattern_match.group(1)
            parse_alt('RPM::Hold:: "' + pattern + '";')
            entries.add(pattern)
    pattern = canonical_pattern(name)
    if desired == 'held':
        entries.add(pattern)
    else:
        entries.discard(pattern)
    if entries:
        content = 'RPM::Hold {\n' + ''.join('    "' + entry + '";\n' for entry in sorted(entries)) + '};\n'
        if content == original:
            return False
        atomic_write(path, content.encode('utf-8'))
        return True
    elif os.path.exists(path):
        os.unlink(path)
        directory_fd = os.open(os.path.dirname(path), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return True
    return False


@contextmanager
def mutation_lock(timeout, path='/run/package-hold-control.lock'):
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise HoldError('permission_denied', str(exc)) from exc
    try:
        end = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= end:
                    raise HoldError('concurrent_change', 'Mutation lock timeout') from exc
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


def execute(packages, backend, state='query', check_mode=False, adapter=None, lock_timeout=10):
    result = {'schema_version': 1, 'changed': False, 'failed': False, 'check_mode': check_mode,
              'backend': backend, 'operation': state, 'packages': [], 'errors': []}
    adapter = adapter or Backend(backend)
    try:
        validate_packages(packages, backend)
        rows, inventory = adapter.query(packages)
        result['packages'] = [dict(row, before_state=row['state'], changed=False, would_change=False) for row in rows]
        if state != 'query':
            for row in list(result['packages']):
                if not adapter.needs_change(row, inventory[row['package']], state):
                    continue
                if check_mode:
                    row['would_change'] = True
                    result['changed'] = True
                    continue
                try:
                    with mutation_lock(lock_timeout):
                        # Re-query under lock and use fresh build arguments. Retry a
                        # changing observation once; never mutate unknown state.
                        fresh_rows, fresh_inventory = adapter.query([{'id': row['id'], 'name': row['package']}])
                        if not fresh_rows:
                            result['packages'].remove(row)
                            continue
                        fresh = fresh_rows[0]
                        if fresh['state'] == 'unknown':
                            row.update(state='unknown', error=fresh['error'])
                            continue
                        if not adapter.needs_change(fresh, fresh_inventory[row['package']], state):
                            row.update(state=state, error=None)
                            continue
                        confirm_rows, confirm_inventory = adapter.query([{'id': row['id'], 'name': row['package']}])
                        if confirm_rows != fresh_rows or confirm_inventory != fresh_inventory:
                            fresh_rows, fresh_inventory = confirm_rows, confirm_inventory
                            confirm_rows, confirm_inventory = adapter.query([{'id': row['id'], 'name': row['package']}])
                            if confirm_rows != fresh_rows or confirm_inventory != fresh_inventory:
                                raise HoldError('concurrent_change', 'Package state changed during query')
                        if not confirm_rows:
                            result['packages'].remove(row)
                            continue
                        if confirm_rows[0]['state'] == 'unknown':
                            row.update(state='unknown', error=confirm_rows[0]['error'])
                            continue
                        if not adapter.needs_change(confirm_rows[0], confirm_inventory[row['package']], state):
                            row.update(state=state, error=None)
                            continue
                        # A failed mutation can have written partial state; report
                        # changed conservatively once a modifying operation starts.
                        row['changed'] = True
                        result['changed'] = True
                        mutated = adapter.mutate(row['package'], fresh_inventory[row['package']], state)
                        if mutated is False:
                            row['changed'] = False
                            result['changed'] = any(item['changed'] for item in result['packages'])
                        final_rows, final_inventory = adapter.query([{'id': row['id'], 'name': row['package']}])
                        if not final_rows:
                            result['packages'].remove(row)
                            continue
                        final = final_rows[0]
                        row.update(state=final['state'], error=final['error'])
                        if final['state'] != state or adapter.needs_change(final, final_inventory[row['package']], state):
                            code = ('external_hold_conflict' if backend == 'alt_rpm_hold'
                                    and state == 'unheld' and final['state'] == 'held' else 'verification_failed')
                            row['error'] = final['error'] or {'code': code, 'message': 'Hold postcondition not met'}
                except HoldError as exc:
                    row.update(state='unknown', error=exc.error)
                except OSError as exc:
                    row.update(state='unknown', error={'code': 'permission_denied' if exc.errno == errno.EACCES
                                                      else 'command_failed', 'message': str(exc)})
    except HoldError as exc:
        result['errors'].append(exc.error)
    result['failed'] = bool(result['errors'] or any(row['error'] for row in result['packages']))
    return result
