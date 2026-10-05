# Примеры использования

Все команды ниже выполняются из корня проекта. На controller уже установлен Ansible;
[проверенная конфигурация и требования Python](../docs/compatibility.md).
Локальные роли и модули подключает `ansible.cfg`; установка collection не нужна.

1. Замените адреса и SSH-пользователей в [hosts.yml](inventory/hosts.yml).
2. Настройте единственный `package_hold_packages` в
   [group_vars/all/package_hold.yml](inventory/group_vars/all/package_hold.yml).
   Проверенный список для РЕД ОС 7.3, ALT p10 и Astra SE 1.7:
   [package_hold_packages.yml](package_hold_packages.yml);
   [результаты проверки, источники и варианты](package_hold_packages.md).
   Перенесите нужные записи с учётом установленной ветки Kubernetes/PostgreSQL.
   Укажите Python 3.8 через `ansible_python_interpreter`, если системный Python другой.
3. Выполните audit. После проверки результата запустите remediate или полный deployment.

```bash
# Только проверки, с CSV на controller
python3 scripts/run-report.py -i examples/inventory/hosts.yml --phase prerequisites --mode audit
python3 scripts/run-report.py -i examples/inventory/hosts.yml --phase hold --mode audit

# Установка prerequisites и обеспечение hold, с отдельными CSV
python3 scripts/run-report.py -i examples/inventory/hosts.yml --phase prerequisites --mode remediate
python3 scripts/run-report.py -i examples/inventory/hosts.yml --phase hold --mode remediate

# Прямой вызов всех трёх ролей: prerequisites + hold + exporter
ansible-playbook -i examples/inventory/hosts.yml examples/roles.yml --check
ansible-playbook -i examples/inventory/hosts.yml examples/roles.yml

# Только exporter, затем его удаление
ansible-playbook -i examples/inventory/hosts.yml playbooks/exporter.yml
ansible-playbook -i examples/inventory/hosts.yml playbooks/exporter.yml -e package_hold_exporter_state=absent
```

[roles.yml](roles.yml) меняет состояние: устанавливает недостающие prerequisites,
удерживает уже установленные configured packages и разворачивает exporter.
Он не устанавливает сами monitored packages и не формирует CSV. Для отчётов нужен
wrapper с отдельными init/finalize plays. `--check` показывает план; CSV не создаётся.
Для mutation/deployment нужны root/become; добавьте `--ask-become-pass`, если необходимо.

[specification.yml](specification.yml) проверяет интерфейсы ролей с их defaults;
для обычного deployment используйте явно заданные modes из `roles.yml`.
[exporter-config.json](exporter-config.json) показывает формат standalone exporter;
при установке роль генерирует JSON из общего inventory, вручную дублировать mapping не нужно.

Описание параметров каждой роли:
[prerequisites](../roles/package_hold_prerequisites/README.md),
[control](../roles/package_hold_control/README.md),
[exporter](../roles/package_hold_exporter/README.md).
Настройка [Prometheus/Grafana/Zabbix](../docs/monitoring.md),
полные операции и ограничения — в [README](../README.md).
