"""Controller-only CSV aggregation; a single writer after all serial batches."""
import csv
import io
import os
import uuid
from datetime import datetime, timezone
from ansible.module_utils.hold import atomic_write

FIELDS = {
    'hold': ['run_id', 'host', 'os_id', 'backend', 'package_id', 'package', 'state', 'before_state',
             'changed', 'would_change', 'check_mode', 'error_code'],
    'prerequisites': ['run_id', 'host', 'os_id', 'os_version', 'backend', 'mode', 'component',
                      'available_before', 'available_after', 'changed', 'would_change', 'check_mode', 'error_code'],
    'hosts': ['run_id', 'host', 'phase', 'success', 'error_code', 'message'],
}


def csv_document(fields, rows):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
    writer.writeheader()
    for row in rows:
        escaped = {}
        for key in fields:
            value = row.get(key, '')
            if isinstance(value, str) and value.startswith(('=', '+', '-', '@', '\t', '\r')):
                value = "'" + value
            escaped[key] = value
        writer.writerow(escaped)
    return stream.getvalue().encode('utf-8')


def aggregate(run_id, phase, hosts):
    rows, coverage = [], []
    for host, data in hosts.items():
        result = data.get('result', {})
        platform = data.get('os', {})
        if result.get('run_id') != run_id:
            coverage.append({'run_id': run_id, 'host': host, 'phase': phase, 'success': False,
                             'error_code': 'missing_result', 'message': 'Unreachable, interrupted or missing current-run result'})
            continue
        success = result.get('success', not result.get('failed', False))
        errors = list(result.get('errors', []))
        if phase == 'hold':
            for package in result.get('packages', []):
                error = package.get('error')
                if error:
                    errors.append(error)
                rows.append({'run_id': run_id, 'host': host, 'os_id': platform.get('id', ''),
                             'backend': result.get('backend', ''), 'package_id': package['id'],
                             'package': package['package'], 'state': package['state'],
                             'before_state': package['before_state'], 'changed': package['changed'],
                             'would_change': package['would_change'], 'check_mode': result['check_mode'],
                             'error_code': error['code'] if error else ''})
        else:
            for component in result.get('components', []):
                error = component.get('error')
                if error:
                    errors.append(error)
                rows.append({'run_id': run_id, 'host': host, 'os_id': result.get('os', {}).get('id', ''),
                             'os_version': result.get('os', {}).get('version_id', ''),
                             'backend': result.get('backend') or '', 'mode': result.get('mode', ''),
                             'component': component['name'], 'available_before': component['available_before'],
                             'available_after': component['available_after'], 'changed': component['changed'],
                             'would_change': component['would_change'], 'check_mode': result['check_mode'],
                             'error_code': error['code'] if error else ''})
        if not success and not errors:
            errors = [{'code': 'host_failed', 'message': result.get('msg', 'Host task failed')}]
        coverage.append({'run_id': run_id, 'host': host, 'phase': phase,
                         'success': bool(success and not errors), 'error_code': '', 'message': ''})
        for error in errors:
            coverage.append({'run_id': run_id, 'host': host, 'phase': phase, 'success': False,
                             'error_code': error['code'], 'message': error['message']})
    return rows, coverage


def new_run_id():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + str(uuid.uuid4())


def write_reports(directory, run_id, phase, hosts):
    rows, coverage = aggregate(run_id, phase, hosts)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    atomic_write(os.path.join(directory, phase + '.csv'), csv_document(FIELDS[phase], rows), 0o600)
    atomic_write(os.path.join(directory, 'hosts.csv'), csv_document(FIELDS['hosts'], coverage), 0o600)
    return not any(not row['success'] for row in coverage)
