# package_hold_exporter

Run from the project root with its `ansible.cfg`; keep `library/`, `module_utils/`,
`filter_plugins/` and the exporter source alongside `roles/`. No collection installation.

Present installs independent scan timer/oneshot and cache-only HTTP reader. Absent stops timer/services first and removes owned files, cache and the dedicated user; holds remain. Shared directories and administrator files are preserved.

Tested controller environment: ansible-core 2.13.13, Python 3.8.20.
Newer Ansible versions are not blocked, but have not been tested separately.

Shared inventory, privileges and full examples: [README](../../README.md).

| Variable | Default |
|---|---|
| `package_hold_packages` | required inventory; no default |
| `package_hold_exporter_state` | `present` |
| `package_hold_exporter_python` | `/usr/bin/python3` |
| `package_hold_exporter_scan_interval_seconds` | `300` |
| `package_hold_exporter_scan_timeout_seconds` | `60` |
| `package_hold_exporter_command_timeout_seconds` | `30` |
| `package_hold_exporter_listen_address` | `127.0.0.1` |
| `package_hold_exporter_listen_port` | `9817` |
| `package_hold_exporter_user` | `package-hold-exporter` |
| `package_hold_exporter_binary_path` | `/usr/local/libexec/package-hold-exporter.py` |
| `package_hold_exporter_config_path` | `/etc/package-hold-exporter/config.json` |
| `package_hold_exporter_status_path` | `/var/lib/package-hold-exporter/status.json` |
| `package_hold_exporter_zabbix_enabled` | `false` |
| `package_hold_exporter_zabbix_include_dir` | `/etc/zabbix/zabbix_agentd.d` |
| `package_hold_exporter_zabbix_service` | `zabbix-agent` |
| `package_hold_exporter_group` | `{{ package_hold_exporter_user }}` |

Tags: `pre-req, user, install, config, never/clean`.

```yaml
- hosts: package_hold_targets
  become: true
  vars:
    package_hold_packages:
      - id: bash
        names: {redos: bash, astra: bash, alt: bash}
  roles:
    - package_hold_exporter
```

Molecule uses Docker and a native systemd image via `MOLECULE_IMAGE`.
Exporter checks installed bash and an absent name, native scanner under systemd
hardening, cache/HTTP/idempotence/removal; prerequisites/control
need native prerequisites or a reachable repository. Solver/MAC require VM acceptance.
Run clean in a dedicated play because it ends that play.
