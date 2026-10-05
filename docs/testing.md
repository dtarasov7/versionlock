# Проверки и тестовая стратегия

Реализованы runtime tests на Python 3.8 с injected runner, реальным временным
filesystem, clock, HTTP sockets и CLI subprocess. Module и exporter проверяются
независимо на одних golden fixtures. Synthetic fixtures не выдаются за stdout
реальных целевых ОС. RPM key metadata case дополнен наблюдением RED OS 7.3.6 Docker.
Проверенная конфигурация controller: ansible-core 2.13.13 и Python 3.8.20.
Более новые версии отдельно не проверялись; запрета на их запуск в ролях нет.

## Матрица runtime и acceptance

| Область | Cases / ожидаемый результат |
|---|---|
| Inventory | разные names, null, [], duplicate id/name, unknown OS, missing mapping => validation failure |
| Installed | held, unheld, отсутствующий, residual dpkg config, multiarch, multiple rpm installed builds |
| Failure | nonzero rc, timeout, malformed/invalid UTF-8/truncated output, missing executable/plugin, EACCES => unknown/host error |
| RED OS | disabled plugin/global plugins, configured locklist, comments, literal NEVRA, raw wildcard, negative rule, obsolete exact lock, offline add/delete |
| Astra | hold/install/deinstall selection, config-files, broken state, multiarch aggregation |
| ALT | merged dump, native POSIX ERE/ICASE/classes, invalid regex, own/external broad hold, preserved backslashes, late clear, wrong parts dir |
| Module | query no mutation, held twice idempotent, explicit unheld twice, check_mode no writes, postcheck fail preserves changed |
| Races | package disappears before/after mutation, rpmdb/config changed, own lock contention, external writer |
| Cache | success, empty, partial error, full error, attempt marker crash, corrupt/missing/stale, digest/boot reset |
| Atomic write | fsync before replace, directory fsync, temp cleanup, disk-full before/after rename; readers видят старый или новый целый JSON |
| HTTP | /metrics valid text and escaping, no subprocess spy, health-only invalid cache, 404, simultaneous reads |
| Zabbix | snapshot no subprocess, installed-only LLD, unknown status dropped, lost items disabled, invalid cache health |
| CSV | no absent rows, newline/comma/formula escaping, check_mode truthful, serial batch, unreachable coverage, parallel run dirs |
| Alert | один UNHELD при 100 scrape => no alert; U/H; U/U/U => persistent; U/error/U; U/stale; reboot/digest/gap reset |
| Uninstall | timer stopped first, own files removed, no hold changes, user-owned config untouched |

Для каждого backend case module query и exporter query обязаны дать одинаковые rows
и error codes. Mutation cases используют snapshots before/after + ожидаемый argv;
тесты не принимают stub runner всегда rc=0 как доказательство idempotency.

## Локальные пути и зависимости

`ansible.cfg` подключает `roles/`, `library/`, `module_utils/`, `filter_plugins/`.
`scripts/check.py` не собирает и не устанавливает collection, не создаёт namespace symlinks.
Unit tests загружают локальные helpers, Ansible integration проверяет передачу custom
module/module_utils на managed hosts. Wrapper regression запускается из другого каталога.

Обычный запуск предполагает уже установленный Ansible. `requirements.txt`
фиксирует controller dependencies; `requirements-dev.txt` предназначен для разработки. Molecule дополнительно устанавливает `community.docker` и
`ansible.posix` по scenario requirements: это зависимости только контейнерных tests,
а не способ упаковки или установки данного решения. `role_name_check: 2` отключает
требование Galaxy namespace для локальных ролей.

## Запуск имеющихся проверок

```bash
python3 scripts/check.py
promtool check rules monitoring/prometheus/package-hold.rules.yml
promtool test rules monitoring/prometheus/package-hold.test.yml
ANSIBLE_LOCAL_TEMP="$PWD/.build/ansible-tmp" ansible-playbook --syntax-check -i examples/inventory/hosts.yml examples/specification.yml
```

Python tests требуют PyYAML/jsonschema только на controller development environment.
Remote exporter остаётся standard-library. Runtime suite выполняется на Python 3.8.
Molecule сценарии реализованы: exporter default проверяет установленный bash и заведомо отсутствующее имя,
systemd/cache/HTTP/check-mode removal и idempotence; native prerequisites/control
нуждаются в штатном backend package или доступном repository.
Контейнер generic Linux подтверждает только service/files/HTTP; он не заменяет ALT
APT/RED OS DNF patchset. VM acceptance факультативен для основной unit suite, но
обязателен для заявления production support соответствующего профиля.

Native ALT regression сравнивает оба matcher с установленным `HoldPackage` APT
и проверяет настоящий hold/unhold с отдельными временными config файлами.
Read-only контейнер использует tmpfs только для workspace test directory:

```bash
mkdir -p .build/alt-posix-native
docker run --rm --network none --read-only \
  --mount "type=bind,src=$PWD,dst=/workspace,readonly" \
  --tmpfs /workspace/.build/alt-posix-native \
  --env PYTHONDONTWRITEBYTECODE=1 molecule-alt:p10 \
  python3 /workspace/tests/integration/alt-posix.py
```

Zabbix template проверяется YAML/UUIDv4 tests; реальный API import и повторный import
пройдены на Zabbix 7.0.31. Trigger scheduling требует runtime acceptance. Grafana JSON parsing не доказывает rendering;
нужны Grafana 12 и Prometheus с sample series. Unit alert model не заменяет promtool.

## Итоговая проверка архитектуры

* Один inventory список, null отличается от absence; module/result/cache не имеют NOT_INSTALLED.
* Только installed rows; невозможность определить installed — host error, не ложное отсутствие.
* DNF exact fixed lock — hold, без сравнения allowed/installed версий.
* ALT отделён от dpkg; POSIX matcher проверен на p10; solver semantics остаётся provisional.
* Scanner только query, module умеет mutation; общей Python реализации нет.
* Повторные scrape и Zabbix poll не увеличивают streak; stale подавляет пакетные alerts.
* Full failure не сохраняет ложный свежий held; last_success сохраняется отдельно.
* Partial error не теряет остальные установленные пакеты и не создаёт false zero.
* Final CSV writer отдельный play, поэтому serial не перезаписывает batch results.
* Нет version labels/policies, transaction gate или requirements к чужому update workflow.

## Controller integration

`tests/integration/controller.yml`: два serial batches и missing-host coverage,
проверка одного полного CSV. Повтор с `--check` не создаёт reports.
`tests/integration/absent-vars.yml`: native batch inventory query на текущем RED OS
host с заведомо отсутствующим binary package, полный wrapper/control/finalize flow.
Тесты создают `.build` для временных файлов, cache и логов. Этот каталог
не входит в поставку и может быть удалён после проверок. Обычный запуск ролей
не зависит от `.build`; Ansible использует свои стандартные temporary paths.

```bash
ansible-playbook -i tests/integration/inventory.yml tests/integration/controller.yml
ansible-playbook -i tests/integration/inventory.yml tests/integration/controller.yml --check
python3 scripts/run-report.py -i tests/integration/inventory.yml --serial 1 -e @tests/integration/absent-vars.yml
```

Фактические результаты, включая failed external prerequisites install,
перечислены в [verification.md](verification.md). Матрица выше включает и VM-only
cases; наличие строки в таблице не означает выполненный acceptance.

## Regression suite независимого review

`test_end_to_end.py`: единый mapping A held / B unheld / C absent на всех трёх ОС;
CSV, metrics, CLI LLD и persistent evidence. Реальные результаты scanner передаются
в promtool для transient и persistent сценариев. `test_zabbix.py` выполняет JavaScript
preprocessing через Node.js и проверяет trigger arithmetic; это не импорт в Zabbix server.
`test_exporter.py` проверяет concurrent HTTP/atomic writes, предел slow clients и IPv6.
`test_runner.py` проверяет disposable DNF scratch и отсутствие config-path overrides.

`tests/integration/report-unreachable.py` запускается из `scripts/check.py`, если доступен
Ansible: единственный target теряется после инициализации роли, обе фазы сохраняют
ошибочный coverage; check mode не создаёт CSV. Проверка требует localhost SSH/socket.
`tests/integration/dnf-delete.py` запускается Molecule в disposable RED OS и проверяет
нативный delete на приватном locklist: name/version collision, повтор, сохранность system locklist.
Control Molecule теперь выполняет настоящий unhold и повторную проверку idempotence.

Точные результаты последнего запуска: [senior-review.md](senior-review.md).
