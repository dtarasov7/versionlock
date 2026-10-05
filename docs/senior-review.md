# Независимый review и hardening

> Рабочий каталог `.build` с логами и test artifacts удалён после проверок.
> Упомянутые ниже пути описывают артефакты соответствующих прогонов.

> Результаты этапа до удаления collection. Текущий способ запуска и структура:
> [README](../README.md), [CHANGELOG](../CHANGELOG.md).

Дата: 2026-10-03. Исходный снимок сохранён в `.build/senior-review-baseline` до изменений.
Git metadata в workspace нет. Итоговый diff и список изменённых файлов:
`.build/senior-review.diff`, `.build/senior-review-changed-files.txt`.

## Найденные проблемы и исправления

P1 ниже означает блокирующий дефект перед production release, а не оценку CVSS.

| Приоритет | Воспроизводимая проблема | Исправление и проверка |
|---|---|---|
| P1 | DNF delete по имени `tool-2` также сопоставляет NAME-VERSION для пакета `tool` версии 2 и может снять чужой lock | Передаются полные exact positive records выбранного имени, literal arch wildcard экранируется как `[*]`. Unit regression и настоящий DNF delete на приватном locklist сохранили оба посторонних имени и system locklist |
| P1 | ALT игнорировал named children `RPM::Hold::admin` и case-insensitive keys: действующий hold превращался в unheld | Обе реализации нормализуют keys и читают все direct children. Nested children консервативно unknown. Проверено против native ALT `HoldPackage`, не только Python fixtures |
| P1 | Scanner RED OS с установленным пакетом падал под ProtectSystem=strict: DNF пишет logs/cache даже при query; `dnf --version` открывает hawkey.log до CLI overrides | Identity фактического executable проверяется через RPM owner query; query logdir/cachedir/persistdir изолированы во временном каталоге. Systemd hardening сохранён. Непустой RED OS scanner успешно выполняется |
| P1 | Если все targets unreachable, Ansible прекращал playbook до localhost finalize; начальная пустая role observation могла выглядеть успешной | Managed play сохраняет управление при unreachable, роли записывают host_unreachable; начальный result failed/missing_result. Полный wrapper regression проверяет обе фазы и check mode |
| P2 | При partial multiarch hold aggregate state уже unheld, поэтому explicit unhold ничего не делал | Mutation/check mode учитывают каждую архитектуру и оставшиеся exact DNF records. Postcondition проверяет отсутствие оставшегося hold; rc=0 при unchanged partial state считается ошибкой |
| P2 | Неполный DNF config публиковал частичный snapshot; query обращался к ещё не созданным атрибутам | Snapshot становится доступен только после успешных probes/read. Ошибки возвращаются как unknown с исходным error code |
| P1 | Zabbix 7.0 отклонял импорт: UUID объектов имели version 5, API требует UUIDv4 | Исправлены UUID всех объектов, regression проверяет version/variant. Реальный import и повторный import через API Zabbix 7.0.31 успешны |
| P2 | Zabbix discovery принимал marker/stale/failed inventory за отсутствие всех пакетов и немедленно отключал items | Snapshot содержит discovery_valid; discovery preprocessing DISCARD_VALUE при недостоверном inventory. Достоверное отсутствие по-прежнему возвращает [] |
| P2 | Zabbix stale expression мгновенно срабатывал на каждом marker: now-0 превышало threshold | Age branch требует last_completed>0. Package triggers дополнительно отвергают future timestamps |
| P2 | ThreadingHTTPServer не ограничивал число медленных клиентов; допустимый literal IPv6 bind падал на IPv4 socket | 16 одновременных clients, 5s socket timeout, освобождение слотов после ошибок, выбор IPv6 address family. Реальные socket regressions |
| P2 | Wrapper позволял обойти защиту полного отчёта через `-tconfig`, `-lhost` и сокращённые long options | Запрещены attached short options и сокращения task-selection flags; regression до запуска Ansible |
| P2 | После изменения code/config cache мог оставаться invalid до очередного timer, создавая health alerts при rollout | Handler асинхронно инициирует scan после обновления. HTTP запускается независимо; второй converge не меняет состояние |
| P3 | Grafana показывала возраст от UNIX epoch во время marker; не было отдельной таблицы persistent packages | Age не вычисляется для sentinel 0; добавлена persistent table с freshness/up guards. PromQL проверен promtool, dashboard импортирован Grafana 12 |

Исходные unit tests проходили: 62 теста. Новые backend regressions против сохранённого
исходного кода дали 7 assertion failures и 1 error в четырёх test methods:
`.build/review-baseline-regressions.log`. Это воспроизведение дефектов, не финальный статус.

Первый усиленный exporter Molecule обнаружил RED OS backend failure, которого не видел
прежний empty-inventory сценарий. ALT retry понадобился из-за Jinja parsing строки
с LLD macro в новом test assertion; assertion исправлен, финальный прогон прошёл.

## Сохранённые инварианты

- Только hold установленных пакетов. Нет policy/expected version/history/transaction gate.
- Единственный источник конфигурации ролей — `package_hold_packages`. Resolved arrays
  и generated exporter JSON являются производными одного mapping, а не вторым inventory.
- Роли получают strict OS identity через Ansible fact `package_hold_platform` из
  platform module; exporter независимо читает os-release. ID_LIKE не расширяет поддержку.
- Ansible backend и exporter остаются разными Python implementations. Общие fixtures
  проверяют одинаковые semantic results. ALT не вызывает Debian apt-mark.
- HTTP и Zabbix читают cache; никакой request не выполняет package-manager commands.
- Ошибка проверки не превращается в status=0. При неизвестной установленности нет
  вымышленных package rows; подтверждённо отсутствующий пакет не появляется в CSV/metrics/LLD.
- Query и check mode не меняют package/config state. DNF query использует только
  disposable scratch для собственных служебных файлов; native system locklist сохранён.

Consistency search проверил runtime, roles, schemas, monitoring и docs. `expected_version`
остался только в отрицательном schema test, который проверяет его отклонение; исходные
`prompt*.md` содержат исторические требования; они не используются runtime. Запрещённого
поведения/метрик/alerts в runtime нет. `backend-contract.md` приведён в соответствие коду.

## Regression tests

Добавлены/расширены:

- `test_backends.py`: ALT named/case-insensitive keys, nested unknown, incomplete DNF
  config, RPM executable identity и отсутствие `dnf --version` в query.
- `test_module.py`: partial multiarch unhold/check mode/postcondition, DNF NAME-VERSION collision.
- `test_end_to_end.py`: один A/B/C mapping для трёх ОС; A held, B unheld, C absent;
  CSV, metrics, настоящий CLI LLD, persistent evidence. Реальные scan outputs подаются в promtool.
- `test_zabbix.py`: выполнение template JavaScript через Node.js, discovery unknown/absence,
  missing/null values, stale-marker trigger, future/stale/transport guards.
- `test_exporter.py`: concurrent HTTP reads во время atomic replacement, bounded slow
  clients и восстановление capacity, IPv6 bind. Существующие SIGTERM/atomic/fsync/error tests сохранены.
- `test_runner.py`: реальные subprocess и disposable DNF scratch без подмены backend config.
- `test_wrapper.py`: task-selection bypasses; `test_specification.py`: parsing всех dashboard PromQL.
- `report-unreachable.py`: обе report phases, потеря единственного target после init,
  fail coverage, header-only package CSV, отсутствие файлов в check mode.
- `dnf-delete.py`: настоящий plugin delete на приватных config/locklist, preservation
  посторонних records, повторный delete и проверка неизменности system locklist.
- Native ALT test дополнен тремя named/case-insensitive configurations; Molecule
  control выполняет настоящий unhold/repeat, exporter использует bash + отсутствующее имя.

## Проверки текущего состояния

Controller/runtime suite: Python 3.8.20, ansible-core 2.13.13. Более новый Ansible
для подтверждения проверенной конфигурации не применялся.

| Проверка | Результат / свидетельство |
|---|---|
| Unit/runtime | PASS: 81 tests, `.build/review-check.log` |
| Python 3.8 grammar + compilation | PASS: ast.parse(feature_version=(3,8)) и compile для runtime/scripts/tests, реальное выполнение suite на 3.8.20 |
| Ansible syntax | PASS: report.yml, exporter.yml, examples/specification.yml |
| Unreachable report integration | PASS: hold/prerequisites × normal/check; `.build/review-unreachable.log` |
| Serial CSV integration | PASS: normal/check, check changed=0; `.build/review-controller*.log` |
| ansible-lint 6.8.7, yamllint 1.35.1 | PASS: 0 lint failures; `.build/review-ansible-lint.log`, `.build/review-yamllint.log` |
| promtool | PASS: существующие 5 rules, fixture rule tests, actual scan → alert regressions, все dashboard PromQL |
| Native ALT APT | PASS: 14 patterns × 15 names, 3 key variants, late clear, hold/unhold/conflict; `.build/review-alt-native.log` |
| Native DNF delete | PASS: `.build/review-dnf-delete-native.log`; также включён в control Molecule |
| ALT control/exporter Molecule | PASS: full lifecycle, idempotence, непустой scanner и removal; `.build/senior-review-final/results.json` |
| Astra control Molecule | PASS: prerequisites, native held/unhold/idempotence/check mode на image Python 3.7.3; не доказательство заявленной конфигурации Python 3.8 |
| RED OS control Molecule | PASS: prerequisites, native held/unhold/idempotence/check mode/exact delete |
| RED OS exporter | PASS после исправления: converge, idempotence, verify, destroy; scanner с bash под ProtectSystem=strict, absent package исключён |
| Grafana 12.0.2 | PASS: API показывает provisioned=true, 12 panels; `.build/review-grafana-import.json` |
| Zabbix 7.0.31 | PASS: настоящий API import + повторный import, 10 items / 1 LLD / 3 item prototypes / 4 triggers / 2 trigger prototypes; `.build/review-zabbix-import.json` |
| Collection build | PASS: `.build/review-dist/versiolock-package_hold-0.2.0.tar.gz` |

Native images: RED OS `molecule_local/redos:7.3.6` (Python 3.8.20), ALT `molecule-alt:p10`
(Python 3.9.20), Astra `molecule-astra:1.7.10` (Python 3.7.3). Это разные runtime profiles.
Python 3.7 поддержкой exporter не объявлен: актуальное задание требует Python 3.8.

## Alert timing

Default I=300s. Scanner выставляет persistent только после >=3 последовательных
unheld observations, span>I и без gap>I+D+2. Scrape/poll не увеличивает streak.

| Время | Кратковременное снятие | Постоянное снятие |
|---|---|---|
| 12:00 scan | held | held |
| 12:05 scan | unheld, streak=1 | unheld, streak=1 |
| 12:05:20 | hold восстановлен между scans | остаётся unheld |
| 12:10 scan | held, streak=0 | unheld, streak=2 |
| 12:15 scan | held | unheld, streak=3, persistent=1 |
| около 12:16 | основной alert не возникает | Prometheus fire после for:1m и scrape/evaluation задержки |

Zabbix использует тот же scanner flag, а не число polls. Неполный/stale cache подавляет
пакетные alerts. Marker может сбросить уже активный Prometheus alert; после следующего
успешного persistent snapshot for начинается заново. Это честно отражено в architecture,
а не описано как сохранение alert через marker. Polling не доказывает непрерывный hold
между scans и не является event auditing.

## Что требует реальных VM / сервисов

Production certification не заявлена. Containers не доказывают solver/MAC поведение:

- RED OS 7.3/8: distro patchsets, RPM DB и plugin profiles, SELinux, solver upgrade/obsoletes.
- Astra 1.7/1.8: штатный Python 3.8, настоящий multiarch и MAC, exporter service acceptance.
- ALT p10/p11: dependency solver с RPM::Hold; p11 effective config и glibc ABI. Backend
  остаётся provisional до подтверждения поведения обычного upgrade/dist-upgrade.
- Zabbix 7.0: LLD lifecycle/trigger scheduling с реальными agent data. API import
  проверен на 7.0.31; Node.js tests проверяют scripts и арифметику, но не runtime scheduling.
- Grafana: визуальная проверка с production labels/data; provisioning не доказывает rendering всех series.

## Минимальный acceptance на трёх disposable VM

1. Для каждой ОС записать os-release, версии Python/package manager/plugin/systemd;
   создать snapshot VM и локальный тестовый repository. Проверять под конечными MAC policy.
2. Единый inventory A/B/C: A установлен+held, B установлен+unheld, C отсутствует.
   Audit должен дать A/B в CSV/metrics/LLD, только B потенциально alertable, без mutation.
3. Remediate B → held, повтор → changed=false; check-mode unhold → только план;
   настоящий unhold → все installed architectures свободны, повтор → changed=false.
4. RED OS: NAME-VERSION collision и сторонние locks; disabled/missing plugin → unknown.
   Astra: несколько архитектур с partial hold. ALT: named/default/broad regex, `+`/`.`
   в имени, late clear; удалить только own canonical entry, внешний hold → conflict.
5. Прогнать обычные upgrade/dist-upgrade и dependency upgrade toy-b→toy-a на snapshot
   с локальным repo. Проверить hold без bypass flags. Это solver acceptance, не новая version policy.
6. Проверить scanner под финальным systemd sandbox с непустым inventory, cache ownership,
   HTTP/agent от непривилегированных пользователей, SIGTERM, concurrent reads, timeout,
   corrupt/stale cache и потерю связи controller. Не отключать MAC ради прохождения теста.
7. Повторить обе временные последовательности из таблицы с 300s timer. Импортировать
   Zabbix 7.0 template и Grafana dashboard; проверить marker/failed scan без false LLD removal,
   затем удалить C/A и убедиться, что подтверждённое отсутствие убирает текущие rows/alerts.

Детальный solver acceptance и первичные источники: [compatibility.md](compatibility.md).
Перед обновлением Zabbix template развернуть новый exporter с `discovery_valid`;
старый payload будет безопасно отбрасываться discovery preprocessing.
