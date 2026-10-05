# package_hold_prerequisites

Запускайте из корня проекта с его `ansible.cfg`; сохраняйте рядом с `roles/`
каталоги `library/`, `module_utils/`, `filter_plugins/` и исходник exporter.
Установка collection не требуется. [Примеры запуска](../../examples/README.md).

Проверяет штатные executable/package/config capabilities. Audit ничего не устанавливает; remediate устанавливает только missing prerequisites и повторяет probes. Disabled plugin остаётся ошибкой конфигурации.

Проверенная конфигурация controller: ansible-core 2.13.13, Python 3.8.20.
Новые версии Ansible не блокируются, но отдельно не проверялись.

Единый inventory, privileges и полные примеры: [README](../../README.md).

| Variable | Default |
|---|---|
| `package_hold_packages` | required inventory; no default |
| `package_hold_prerequisites_mode` | `audit` |
| `package_hold_prerequisites_command_timeout_seconds` | `30` |
| `package_hold_prerequisites_dnf_plugin_package` | `python3-dnf-plugin-versionlock` |

Tags: `pre-req, config, never/clean`.

```yaml
- hosts: package_hold_targets
  become: true
  vars:
    package_hold_packages:
      - id: bash
        names: {redos: bash, astra: bash, alt: bash}
  roles:
    - package_hold_prerequisites
```

Molecule использует Docker и native systemd image через `MOLECULE_IMAGE`.
Exporter scenario проверяет установленный bash и отсутствующее имя, native scanner
под systemd hardening, cache/HTTP/idempotence/removal;
prerequisites/control требуют native backend prerequisites или доступного repo.
VM acceptance для solver/MAC обязателен. Clean запускайте в отдельном play: end_play.
