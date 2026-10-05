# Фактические проверки

## Автоматический backend модуля — 2026-10-04

`package_hold` самостоятельно выбирает backend по `os-release`; роль control больше
не передаёт backend. Явное значение допускается только как assertion соответствия ОС.

- PASS: 85 tests, Python 3.8 grammar/compile, 4 syntax checks, promtool и report integration.
- Новые entrypoint regressions: automatic selection для RED OS/Astra/ALT,
  совпадающий explicit backend, mismatch без команд, unsupported OS без fallback.
- PASS: реальный Ansible query на RED OS с `packages: []` без `backend`, changed=0.
- `.build`, созданный проверками, удалён после прогона.

> Рабочий каталог `.build` с логами и test artifacts удалён после проверок.
> Упомянутые ниже пути описывают артефакты соответствующих прогонов.

## Локальные роли без collection — 2026-10-04

Проверенная конфигурация сохранена: Python 3.8.20, ansible-core 2.13.13.
Используются только локальные пути из `ansible.cfg`; namespace и bootstrap collection удалены.

| Проверка | Результат |
|---|---|
| `python3 scripts/check.py` | PASS: 81 tests, Python 3.8 grammar/compile, 4 syntax checks, promtool, 4 unreachable report cases |
| Wrapper из другого рабочего каталога | PASS: hold/prerequisites × normal/check; локальные modules найдены, ошибочный coverage сохранён |
| Controller CSV serial integration | PASS: normal/check; check changed=0 |
| ansible-lint 6.8.7 | PASS: 0 failures / 0 lint warnings, 70 files |
| yamllint 1.35.1 | PASS |
| Markdown links | PASS: локальные ссылки в 32 документах |
| RED OS exporter/control Molecule | PASS: полный lifecycle, idempotence, scanner под systemd sandbox, native hold/unhold/exact delete |
| ALT exporter/control Molecule | PASS: полный lifecycle, idempotence, native backend |
| Astra control Molecule | PASS: полный lifecycle, native hold/unhold; image Python 3.7.3 не подтверждает заявленную конфигурацию Python 3.8 |

Логи: `.build/no-collection-check.log`, `.build/no-collection-ansible-lint.log`,
`.build/no-collection-yamllint.log`, `.build/no-collection-controller*.log`,
`.build/no-collection-molecule/results.json`. Все созданные scenario containers удалены.
Collections `community.docker`/`ansible.posix` нужны только Molecule tests;
роли и exporter данного проекта не требуют установки собственной collection.
Ограничения VM/solver/MAC acceptance сохраняются: [compatibility](compatibility.md).

## Историческая проверка 0.2.0

> Ниже сохранены результаты предыдущего этапа. Актуальные результаты независимого
> review и обновлённых сценариев: [senior-review.md](senior-review.md).
> Переход на локальные роли: [CHANGELOG](../CHANGELOG.md), актуальный запуск — [README](../README.md).

Дата: 2026-10-03. Проверенная конфигурация: **Ansible 2.13.13, Python 3.8.20**.
Более новый Ansible не используется для подтверждения совместимости.

| Проверка | Результат |
|---|---|
| `python3 scripts/check.py` | PASS: 62 unit/runtime tests, Python 3.8 grammar всех runtime/test/scripts files, три playbook syntax checks, Prometheus checks |
| `ansible-lint 6.8.7 --offline` | PASS: 73 files, 0 failures, 0 lint warnings; runtime library emits Python 3.8 cryptography deprecation warning |
| `yamllint 1.35.1 .` | PASS |
| `promtool check rules` | PASS: 5 rules |
| `promtool test rules` | PASS: transient/persistent/stale/failed scanner cases |
| Controller CSV integration, `serial: 1` | PASS: оба batches в одном CSV, missing-host coverage признано неуспешным |
| Controller integration `--check` | PASS: changed=0, CSV files не создаются |
| Native RED OS wrapper/control/report, absent package, `serial: 1` | PASS: обе managed-host observations changed=0; hold.csv содержит только header; hosts.csv содержит успешное coverage |
| Exporter Molecule на `molecule_local/redos:7.3.6` | PASS: Docker create/converge/idempotence/verify/destroy; second converge changed=0; timer/scanner/cache/HTTP/Zabbix CLI/removal |
| Native prerequisites/control Molecule на RED OS 7.3.6 | PASS с test-only install timeout 180s: plugin/remediation, native held/query, idempotence и check-mode unhold |
| Native Astra prerequisites/control Molecule | PASS на image Python 3.7.3; заявленная конфигурация managed Python 3.8 этим не подтверждена |
| Native ALT exporter Molecule | PASS на image Python 3.9.20 |
| Native ALT prerequisites/control Molecule | PASS после расширения POSIX matcher: 25.0s / 19.8s, штатный kernel regex сохранён |
| Native ALT POSIX regression | PASS: оба adapters совпадают с установленным HoldPackage на 14 regex × 15 имён; late clear, настоящий hold/unhold/idempotence/check-mode/conflict |
| Exporter absent check mode | PASS: последующий `/healthz` продолжает отвечать; настоящее удаление выполняется отдельным шагом |
| Exporter unsafe destination check | PASS: basename `..` отклонён до removal, changed=0 |
| Grafana 12.0.2 provisioning | PASS: API `/api/dashboards/uid/package-hold` возвращает `provisioned=true`, 11 panels |
| Grafana JSON и Zabbix YAML/UUID validation | PASS |
| Collection build | PASS: `versiolock-package_hold-0.2.0.tar.gz` |

Runtime suite выполняет обе независимые implementations на golden fixtures;
held/unheld/absence/unknown/errors всех backend, multiarch, disabled DNF config,
ALT POSIX rules/classes/case/backslashes и invalid regex, module remediation/unhold/check-mode/idempotence,
atomic writes/fsync/attempt marker, scan partial/total failure, stale/digest/boot reset,
HTTP с запретом package commands, CLI/SIGTERM, quoting/coverage CSV, реальные subprocess
с stderr/returncode/invalid UTF-8/timeout/output cap. Это fixture/runtime доказательства,
а не solver acceptance на всех ОС.

## Реальные ошибки, обнаруженные при проверках

* RED OS rpm 4.18.2 содержит `gpg-pubkey` metadata с arch `(none)`.
  Исправлен parser обеих implementations: узкое documented metadata-исключение,
  остальные malformed rows не игнорируются. Regression test добавлен.
* Default Docker driver использовал newer `community.docker`, несовместимый с
  заявленной конфигурацией. Test requirements закреплены `community.docker 3.4.11` и
  `ansible.posix 1.5.4`; old Docker adapter требует `requests 2.31.0`.
* Controller Molecule lifecycle использует project-owned synchronous create/destroy,
  чтобы не создавать async state в пользовательском HOME.
* Финальный review исправил наследование `config` tag динамическими include и
  запретил basename `.`/`..` в управляемых путях до любого removal.
* Native prerequisite installation на исходном RED OS image не завершилась за 30s:
  `command_timeout`, plugin/config остались missing. Ошибка честно отражена в
  structured result; неизвестный hold не превращён в unheld. Exporter lifecycle
  проверен с допустимым пустым inventory и не зависит от этого external repository.
  Повторный Molecule prerequisites с test-only timeout 180s прошёл, включая idempotence.
  Control на этом же image также прошёл полный Molecule lifecycle.
* Native ALT apt-config dump сохраняет single-backslash patterns других keys.
  Исправлены оба parser, добавлена реальная regression fixture. glibc matcher
  поддерживает штатный kernel/alsa regex с флагами native ALT APT.
  Dump сохраняется буквально, включая single/double backslashes. Общие правила
  переживают изменение own exact hold; unhold с остающимся broad hold сообщает конфликт.

Матрица последующего Molecule прогона на трёх предоставленных images:
[molecule.md](molecule.md). Python внутри Astra/ALT images отличается от заявленной конфигурации Python 3.8;
controller во всех случаях оставался Ansible 2.13.13 / Python 3.8.20.

## Acceptance, который ещё требуется

* RED OS 7.3/8: профили остальных patchsets, настоящий unhold/delete, непустой scanner
  под финальным hardening и solver test. RED OS 7.3.6 container уже подтвердил plugin
  install, offline held/query, idempotence и check-mode unhold на bash.
* Astra 1.7/1.8: native dpkg/apt-mark, multiarch, permission/MAC, root scanner/service.
* ALT p10: dependency solver и VM acceptance; p11: effective config/POSIX и solver.
  p10 matcher, merge/clear и hold/unhold/conflict подтверждены native container.
  Backend остаётся provisional до проверки solver.
* Zabbix 7.0: импорт в реальный server и trigger evaluation. YAML validation не доказывает
  import acceptance. Grafana provisioning подтверждён, rendering с production sample
  series/переменными ещё требует target Prometheus/Grafana.

Полный порядок VM acceptance находится в [compatibility.md](compatibility.md).
Нет утверждения о certified production support непроверенных профилей.

## Повторение проверок

```bash
python3 scripts/check.py
ansible-playbook -i tests/integration/inventory.yml tests/integration/controller.yml
ansible-playbook -i tests/integration/inventory.yml tests/integration/controller.yml --check
python3 scripts/run-report.py -i tests/integration/inventory.yml --serial 1 -e @tests/integration/absent-vars.yml
```

Для Molecule нужен disposable native systemd image и Docker daemon. Выполненный запуск:

```bash
cd roles/package_hold_exporter
MOLECULE_IMAGE=molecule_local/redos:7.3.6 molecule test
```

В этом окружении инструменты lint установлены в `.build` без изменения system Python;
Molecule использует pinned requests через workspace PYTHONPATH. Обычный controller
venv с `requirements-dev.txt` воспроизводит согласованные версии.

Git metadata в предоставленном workspace отсутствует: `git status/diff` возвращает
`not a git repository`. Полное сравнение выполнено с исходным снимком файлов;
локальный review diff и список файлов сохранены в `.build/changes.diff` и
`.build/changed-files.txt`. Репозиторий не инициализировался и история не менялась.
