# Versionlock: native package hold

Набор локальных Ansible-ролей и модулей управляет только hold установленных
пакетов и периодически проверяет его состояние. **Проверенная конфигурация controller:
ansible-core 2.13.13 и Python 3.8.20**. Это версии, на которых выполнены проверки.
В metadata ролей указана минимальная версия ansible-core 2.13; верхнего ограничения нет.
Работа на 2.14, 2.16 и новее отдельно не проверена, поэтому совместимость не подтверждена.

Модули и exporter рассчитаны на Python 3.8+. Выбранная версия ansible-core может
требовать более новый Python на controller или managed hosts; см. [совместимость](docs/compatibility.md).
Exporter — независимый single-file Python, standard library, без Ansible
и дополнительных remote dependencies.

Отсутствие configured package на host является нормальным состоянием и не генерирует
report row, metric, discovery object или alert.

Система является периодическим state monitoring, а не event auditing.
Кратковременный unhold между scan может остаться незамеченным по дизайну.

Контроль версий, история обновлений, transaction gate и auditd не реализуются.
NEVRA используется внутри RED OS mutation только как аргумент штатного DNF lock.

```text
package_hold_packages (inventory)
  ├── prerequisites audit/remediate ─┐
  ├── control audit/remediate ───────┴── localhost finalize → CSV
  └── exporter role → config.json
        systemd timer → scan → atomic status.json
                                 ├── HTTP /metrics → Prometheus/Grafana
                                 └── Zabbix snapshot → dependent LLD/items
```

Код и артефакты готовы для тестового развёртывания. Сертифицированная production
поддержка ОС требует VM acceptance из [compatibility.md](docs/compatibility.md).
ALT `RPM::Hold` поддерживает POSIX regex с семантикой native APT, проверенной на p10.
Backend остаётся **provisional**: безопасность solver при изменении зависимостей
должна быть проверена отдельно.

## Быстрый старт

Скопируйте или скачайте весь проект, затем запускайте Ansible из его корня.
[ansible.cfg](ansible.cfg) подключает локальные `roles/`, `library/`, `module_utils/`
и `filter_plugins/`. Сборка, установка и namespace collection не требуются.
Не копируйте только `roles/`: модули и исходник exporter находятся рядом с ними.

Ansible уже установлен на controller. Замените адреса и SSH-пользователей
в inventory и настройте список пакетов в group_vars, затем выполните audit:

```bash
python3 scripts/run-report.py -i examples/inventory/hosts.yml --phase hold --mode audit
```

Managed hosts требуют Python 3.8+ с учётом требований выбранного ansible-core; зависимости package manager проверяет роль prerequisites.
[Примеры и порядок запуска](examples/README.md), [прямое использование ролей](examples/roles.yml).
Для своих playbooks подключайте пути из `ansible.cfg` или задавайте `ANSIBLE_CONFIG`
на этот файл. Wrapper сам выбирает конфигурацию проекта, поэтому работает и из другого каталога.

## Inventory и операции

[Пример inventory](examples/inventory/hosts.yml) содержит группу `package_hold_targets`.
Все роли используют единственный список в `inventory/group_vars/all/package_hold.yml`:

```yaml
package_hold_packages:
  - id: java17
    names:
      redos: java-17-openjdk
      astra: openjdk-17-jre
      alt: java-17-openjdk
  - id: nginx
    names:
      redos: nginx
      astra: nginx
      alt: null  # явно неприменимо; все три mapping keys обязательны

package_hold_exporter_scan_interval_seconds: 300
package_hold_exporter_listen_address: 127.0.0.1
package_hold_exporter_zabbix_enabled: false
```

`[]` допустим. Имена exact binary names, без arch/version/glob; duplicate id/name,
missing mapping и all-null entry запрещены. [Schema](docs/inventory-schema.md).
Inventory не устанавливает перечисленное ПО. Роли устанавливают только prerequisites
и компоненты мониторинга.

Запускайте reports через wrapper: он проверяет duplicate YAML keys, frozen host list
и присутствие localhost в limit. YAML inventory/group_vars должны находиться рядом;
для dynamic inventory нужна отдельная предварительная проверка источника.

```bash
# Audit prerequisites: никаких install/config changes
python3 scripts/run-report.py -i examples/inventory/hosts.yml --phase prerequisites --mode audit
# Установить отсутствующие prerequisites, затем повторно проверить
python3 scripts/run-report.py -i examples/inventory/hosts.yml --phase prerequisites --mode remediate
# Query установленных packages
python3 scripts/run-report.py -i examples/inventory/hosts.yml --phase hold --mode audit
# Ensure held и проверить postconditions
python3 scripts/run-report.py -i examples/inventory/hosts.yml --phase hold --mode remediate
# Check mode: state остаётся исходным, would_change показывает план; CSV не пишется
python3 scripts/run-report.py -i examples/inventory/hosts.yml --phase hold --mode remediate --check
# serial batches и ограниченная выборка; localhost обязателен
python3 scripts/run-report.py -i examples/inventory/hosts.yml --serial 1 --limit 'redos01.example.test,localhost'
# Exporter lifecycle
ansible-playbook -i examples/inventory/hosts.yml playbooks/exporter.yml
ansible-playbook -i examples/inventory/hosts.yml playbooks/exporter.yml -e package_hold_exporter_state=absent
```

Audit можно выполнить без become: wrapper `-e package_hold_report_become=false` при
достаточных read permissions. Для установки пакетов, mutation, users/units требуется
root/become. System Python bootstrap вне ролей. Disabled DNF plugin не включается
молча. Default command timeout 30s; для установки prerequisites при медленном repo
задайте `package_hold_prerequisites_command_timeout_seconds`, например 180.

Explicit unhold доступен только через module; control role никогда не снимает hold:

ОС и backend модуль определяет самостоятельно по `os-release`. В прямом вызове
задаётся реальное имя пакета для этого host; при использовании ролей имя выбирается
автоматически из общего inventory mapping.

```yaml
- name: Explicit operator unhold on Astra
  package_hold:
    state: unheld
    packages:
      - {id: java17, name: openjdk-17-jre}
```

Все роли поддерживают check mode. Module не создаёт mutation lock и не выполняет
mutating commands в check mode. Exporter role пропускает service starts/restarts
при check mode; file/user tasks только показывают план.

## Reports

Каждый запуск создаёт `reports/<UTC timestamp>-<UUID>/` (0700), CSV (0600, UTF-8 LF).
Writer один, в отдельном localhost play после всех serial batches. `hold.csv`
содержит только подтверждённо установленные packages; ошибки installed query,
unreachable/missing results и покрытие hosts отражаются в `hosts.csv`.
Failing checks завершают play ошибкой **после** записи отчётов. Old fact cache другого
run_id игнорируется. Пример:

```csv
run_id,host,os_id,backend,package_id,package,state,before_state,changed,would_change,check_mode,error_code
20261003T120000Z-<UUID>,astra01,astra,dpkg_selections,java17,openjdk-17-jre,held,unheld,True,False,False,
```

`prerequisites.csv` содержит отдельные capability components до/после проверки.
`csv.writer` экранирует запятые/кавычки/newlines; потенциальные spreadsheet formulas
префиксуются апострофом. Полный controller crash может не оставить итогового отчёта.

## Exporter и мониторинг

Default paths: `/usr/local/libexec/package-hold-exporter.py`,
`/etc/package-hold-exporter/config.json`, `/var/lib/package-hold-exporter/status.json`.
Root scanner пишет cache; system user `package-hold-exporter` и Zabbix только читают.
Используйте выделенного пользователя/группу роли. Timer и HTTP имеют независимый
lifecycle. Scanner service и ручной scan защищены flock; это не блокировка обновлений ОС.

```bash
systemctl status package-hold-scan.timer package-hold-exporter.service
systemctl start package-hold-scan.service
journalctl -u package-hold-scan.service -u package-hold-exporter.service
curl http://localhost:9817/metrics
/usr/bin/python3 /usr/local/libexec/package-hold-exporter.py zabbix-snapshot
/usr/bin/python3 /usr/local/libexec/package-hold-exporter.py zabbix-discovery
/usr/bin/python3 /usr/local/libexec/package-hold-exporter.py zabbix-get --package-id java17 --field status
```

HTTP и все Zabbix subcommands читают cache, без package-manager commands.
`/healthz` — liveness. Missing/corrupt cache даёт HTTP 200 с health-only metrics.
Stale/invalid cache не публикует package series. Unknown публикует check_success=0,
без ложного status=0. Scanner пишет atomic attempt marker и затем atomic final snapshot;
авария после marker не подаёт старый held как свежий успешный scan.

```text
# HELP package_hold_status Installed package hold: 1 held, 0 unheld; unknown omitted.
# TYPE package_hold_status gauge
package_hold_status{package_id="java17",package="openjdk-17-jre",method="dpkg_selections"} 1
# HELP package_hold_check_success Installed package hold check succeeded.
# TYPE package_hold_check_success gauge
package_hold_check_success{package_id="java17",package="openjdk-17-jre",method="dpkg_selections"} 1
package_hold_scan_success 1
package_hold_scan_last_completed_timestamp_seconds 1791028800
package_hold_scan_last_success_timestamp_seconds 1791028800
package_hold_scan_duration_seconds 0.12
```

Установите [Prometheus rules](monitoring/prometheus/package-hold.rules.yml) в каталог
Prometheus и добавьте файл в `rule_files`, проверьте `promtool check rules`, затем
перезагрузите Prometheus. [Scrape example](monitoring/prometheus/scrape.example.yml)
задаёт `job`, `instance`, `env`, `group`. Default HTTP bind loopback: для внешнего
scrape задайте адрес management interface и разрешите доступ средствами инфраструктуры.

Разместите [Grafana JSON](monitoring/grafana/package-hold.json) в
`/var/lib/grafana/dashboards/package-hold/`, а [provider](monitoring/grafana/provider.yml)
в `provisioning/dashboards/`. Dashboard также можно импортировать через UI Grafana 12.
Datasource выбирается переменной `datasource`; `instance` означает host:port scrape
endpoint. Фильтры job/env/group/instance/package_id, таблица held/unheld/unknown,
scanner failure и stale уже включены. Recording rules нужны stale-панели.

Zabbix 7.0: импортируйте [template](monitoring/zabbix/package-hold.yaml), привяжите к
host, включите `package_hold_exporter_zabbix_enabled: true`. Укажите include directory
и service, если используется agent2. Agent config должен включать этот directory;
роль управляет только своим `package-hold.conf` и перезапускает выбранный agent.
Master item `package_hold.snapshot` опрашивается каждые 30s; LLD и items зависимы от
того же snapshot. Unknown status отбрасывается. LLD обновляется только по достоверному inventory;
marker/stale/failed scan не отключают items. Подтверждённо потерянные objects сразу disabled.
Zabbix не запускает scan. Перед включением интеграции agent должен быть установлен.

Persistent UNHELD требует >=3 последовательных scans, охватывающих больше одного
интервала. При 300s scans — примерно 600s до persistent и дополнительное окно 1m.
Один unheld interval не alert-ит. Default stale threshold `2*300+60+2=662s`.
Изменение interval/config/boot, gap, held или unknown сбрасывают evidence. Повторные
scrape/poll не изменяют её. [Monitoring contract](docs/monitoring.md).

При `state=absent` роль сначала отключает timer и services, удаляет owned units,
code/config/cache, Zabbix include и выделенного пользователя/группу, делает daemon-reload.
`status.json` **не сохраняется**. Общие directories и чужие файлы остаются; hold не
снимается. Изменение пользовательских path/name variables требует сначала удалить
deployment со старыми variables, затем установить с новыми.

## Совместимость и проверки

| OS profile | Backend | Проверенность |
|---|---|---|
| RED OS 7.3/8, DNF4 | dnf_versionlock | Fixtures; 7.3.6 Docker Molecule: plugin/held/query/idempotence/check-mode; solver/MAC и другие profiles отдельно |
| Astra SE 1.7/1.8 | dpkg_selections | Fixtures; 1.7.10 Docker prerequisites/control PASS на Python 3.7.3; Python 3.8, VM и multiarch acceptance отдельно |
| ALT p10/p11 | alt_rpm_hold | Native p10 POSIX matcher и prerequisites/control/exporter PASS; provisional, p11 и solver acceptance отдельно |

[Backend contract](docs/backend-contract.md) и [compatibility](docs/compatibility.md)
фиксируют ограничения. Нет обещания защиты от прямого rpm, отключённого DNF plugin
или обхода штатных hold механизмов. Невалидные ALT POSIX patterns и нестандартные
DNF plugin paths дают unknown, не unheld. Общие ALT правила сохраняются при unhold;
если они продолжают удерживать пакет, возвращается `external_hold_conflict`.

```bash
python3 scripts/check.py
ansible-playbook -i tests/integration/inventory.yml tests/integration/controller.yml
ansible-playbook -i tests/integration/inventory.yml tests/integration/controller.yml --check
# Disposable target with native OS/systemd image and available prerequisites
cd roles/package_hold_exporter
MOLECULE_IMAGE=<systemd-image> molecule test
```

Fixture tests выполняют **обе независимые реализации**, module mutation/check mode,
cache atomicity/errors/streak, реальный HTTP/CLI/SIGTERM, CSV и subprocess limits.
Molecule prerequisite/control scenarios требуют доступного native repository или
предустановленного plugin. Exporter scenario использует установленный bash и заведомо отсутствующее имя
и проверяет services/cache/HTTP/idempotence/removal; это не solver acceptance.
Фактические результаты и ограничения: [verification](docs/verification.md).
История изменений: [CHANGELOG.md](CHANGELOG.md).
Прогон на локальных RED OS/Astra/ALT images и команды Molecule:
[molecule](docs/molecule.md).

```text
.
├── ansible.cfg, requirements.txt, requirements-dev.txt, CHANGELOG.md
├── package-hold-exporter.py
├── library/{package_hold,package_hold_prerequisite,package_hold_report}.py
├── module_utils/{hold,prerequisites,report}.py
├── filter_plugins/package_hold.py
├── roles/package_hold_{prerequisites,control,exporter}/
│   ├── defaults/, meta/argument_specs.yml, tasks/, handlers/
│   └── templates/, molecule/default/, README.md, README-en.md
├── playbooks/{report,exporter}.yml, scripts/{run-report,check}.py
├── monitoring/{prometheus,grafana,zabbix}/
├── schemas/, examples/, tests/{fixtures,unit,integration}/
└── docs/{architecture,backend-contract,compatibility,inventory-schema,testing,verification}.md
```

Результаты независимого review, исправления и VM acceptance: [senior-review.md](docs/senior-review.md).
