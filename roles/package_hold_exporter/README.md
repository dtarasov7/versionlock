# package_hold_exporter

Запускайте из корня проекта с его `ansible.cfg`; сохраняйте рядом с `roles/`
каталоги `library/`, `module_utils/`, `filter_plugins/` и исходник exporter.
Установка collection не требуется. [Примеры запуска](../../examples/README.md).

Present устанавливает независимые scanner timer/oneshot и HTTP reader. Absent сначала останавливает timer/services и удаляет owned files, cache и dedicated user; hold остаётся. Cache не сохраняется, общие directories/чужие файлы остаются.

Проверенная конфигурация controller: ansible-core 2.13.13, Python 3.8.20.
Новые версии Ansible не блокируются, но отдельно не проверялись.

Единый inventory, privileges и полные примеры: [README](../../README.md).

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

Molecule использует Docker и native systemd image через `MOLECULE_IMAGE`.
Exporter scenario проверяет установленный bash и отсутствующее имя, native scanner
под systemd hardening, cache/HTTP/idempotence/removal;
prerequisites/control требуют native backend prerequisites или доступного repo.
VM acceptance для solver/MAC обязателен. Clean запускайте в отдельном play: end_play.
