"""One inventory resolver for all three roles."""
import re
from ansible.errors import AnsibleFilterError

BACKENDS = {'redos': 'dnf_versionlock', 'astra': 'dpkg_selections', 'alt': 'alt_rpm_hold'}


def resolve(packages, os_id):
    if os_id not in BACKENDS:
        raise AnsibleFilterError('Unsupported normalized OS')
    if not isinstance(packages, list) or len(packages) > 1024:
        raise AnsibleFilterError('package_hold_packages must be a list of at most 1024 entries')
    ids, names = set(), {system: set() for system in BACKENDS}
    resolved = []
    for item in packages:
        if not isinstance(item, dict) or set(item) != {'id', 'names'}:
            raise AnsibleFilterError('Inventory entries require only id and names')
        package_id, mapping = item['id'], item['names']
        if not isinstance(package_id, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', package_id) or package_id in ids:
            raise AnsibleFilterError('Invalid or duplicate package id')
        ids.add(package_id)
        if not isinstance(mapping, dict) or set(mapping) != set(BACKENDS) or all(value is None for value in mapping.values()):
            raise AnsibleFilterError('All OS names keys required; at least one non-null')
        for system, name in mapping.items():
            if name is None:
                continue
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+\-]{0,254}', name):
                raise AnsibleFilterError('Invalid exact package name')
            if system == 'astra' and not re.fullmatch(r'[a-z0-9][a-z0-9+.\-]+', name):
                raise AnsibleFilterError('Invalid dpkg package name')
            if name in names[system]:
                raise AnsibleFilterError('Duplicate name within OS mapping')
            names[system].add(name)
        if mapping[os_id] is not None:
            resolved.append({'id': package_id, 'name': mapping[os_id]})
    return resolved


class FilterModule:
    def filters(self):
        return {'package_hold_resolve': resolve}
