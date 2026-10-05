# Monitoring contract v1

Артефакты используют реализованный cache-only exporter; scanner и readers имеют независимый lifecycle.
Prometheus scrape/evaluation recommended 30s, Zabbix master poll 30s. Оба существенно
меньше I (default 300s). Clock синхронизирован. Общий persistent state вычисляет scanner,
не HTTP/PromQL/Zabbix на основании количества poll.

## Prometheus

Все metrics gauges. Package labels ровно `package_id,package,method`, без version,
error message, scan_id, hostname. job/instance/env/group задаёт scrape configuration.
Одна series каждого типа на логический installed package; O(hosts * installed configured).

| Metric | Семантика |
|---|---|
| package_hold_status | 1 held / 0 unheld; unknown не имеет этой series |
| package_hold_check_success | 1 достоверный hold / 0 unknown; только установленный |
| package_hold_persistent_unheld | 1 если выполнено правило >=3 scans и span>I; иначе 0 |
| package_hold_cache_valid | 1 parse/schema/digest/boot valid, иначе 0 |
| package_hold_scan_success | 1 все проверки последнего completed scan успешны; marker/error=0 |
| package_hold_scan_last_attempt_timestamp_seconds | Начало последнего attempted scan; 0 never |
| package_hold_scan_last_completed_timestamp_seconds | Завершение последнего scan (даже failed); marker=0 |
| package_hold_scan_last_success_timestamp_seconds | Последний полностью успешный scan; 0 never |
| package_hold_scan_duration_seconds | Время последнего завершённого scan, marker=0 |
| package_hold_scan_interval_seconds | I из config |
| package_hold_scan_stale_after_seconds | 2*I+D+2 |

Три package series только из свежего completed cache. Unknown => check_success=0,
persistent=0, status отсутствует. Empty set => только host metrics. Missing/invalid
cache health defaults описаны в architecture. Labels экранируются по Prometheus text
format (`\\`, `\"`, `\n`). Метрики без своих explicit sample timestamps.

Rules `monitoring/prometheus/package-hold.rules.yml` публикуют recording rule
`package_hold:scanner_fresh` (0/1 на job,instance), защищают alerts freshness/up и
не привязаны к имени job. В примере scrape job=package-hold; labels job/instance
используются только для соединения scanner health и up. На одном job/instance ровно один exporter.

Persistent alert использует scanner flag и for:1m. ScannerFailed с for:1m, Stale
с for:1m; отдельный CacheInvalid for:1m. Exporter availability контролируется
инфраструктурным up alert для выбранных scrape targets.
Во время длительного scan marker может вызывать health alert — D=60 и это намеренно
порог затянувшегося/неуспешного scan; пакетный UNHELD не возникает.
`up==0` не видит удалённый из service discovery target: inventory coverage мониторится
на уровне инфраструктуры Prometheus. Grafana использует recording rule; загрузить rules первым.

## Zabbix 7.0

Один статический UserParameter `package_hold.snapshot` вызывает
`zabbix-snapshot --config /etc/package-hold-exporter/config.json` без аргументов от клиента.
Никакого flexible `$1` и shell injection через package id. Include по умолчанию agentd.d,
для agent2 задать include_dir и service. При включённой интеграции агент должен быть установлен; роль создаёт include
и перезапускает agent, но не устанавливает его автоматически. CLI <= agent Timeout, только чтение <=4 MiB.

CLI проецирует cache в payload:

```json
{"cache_valid":1,"fresh":1,"discovery_valid":1,"scan_success":1,"last_attempt":1000,
 "last_completed":1001,"last_success":1001,"duration":1,"interval":300,
 "stale_after":662,"packages":[{"id":"nginx","package":"nginx",
 "method":"dpkg_selections","status":1,"check_success":1,"persistent":0}]}
```

Все flags числа 0/1. Unknown: status=null, check_success=0, persistent=0.
Stale/marker/invalid => packages=[], fresh=0. Остальные health поля сохраняются при
валидном cache; invalid => timestamps/duration=0 и scan_success=0. `discovery_valid=1`
только для fresh completed cache без host-level inventory errors; partial hold errors
не мешают discovery подтверждённо установленных пакетов. LLD preprocessing пропускает
`packages` только при discovery_valid=1, иначе DISCARD_VALUE. Marker, stale и failed
inventory не являются доказательством удаления пакетов и не должны отключать items.
Достоверный пустой inventory передаёт `[]` и запускает lost-resource handling.

Template: один ZABBIX_PASSIVE TEXT master, dependent discovery и items. Status
prototype выбрасывает null/missing samples (не false 0). Check_success prototype
выбрасывает missing samples. Persistent prototype **на missing row выдаёт 0** для
ранее обнаруженного item: это сброс тревоги, не создание записи отсутствующего
пакета в LLD. Lost resources отключаются сразу, удаляются через 1d; старые history
в Zabbix/Prometheus не переписываются. В discovery нет throttling, чтобы удаление
обрабатывалось на каждом достоверном inventory update. У startup до первого LLD dependent items
могут появиться на следующем poll.

Persistent trigger: min(persistent,1m)=1, fresh=1, cache_valid=1,
0<=now-last_completed<=stale_after, nodata(master,2m)=0. Даже если agent перестал
отвечать, старый flag не остаётся активным бесконечно. Health triggers:
scan_success max(1m)=0; stale по last_completed/threshold; invalid cache;
master nodata(2m). Age branch stale trigger применяется только при last_completed>0:
marker с timestamp=0 не создаёт мгновенный ложный stale alert. Для invalid/stale
UNHELD подавляется. Никакого sender protocol.

Шаблон создан по [формату Zabbix 7.0 templates](https://www.zabbix.com/documentation/7.0/en/manual/xml_export_import/templates).
Импорт и повторный импорт проверены через API Zabbix 7.0.31; UUID объектов соответствуют UUIDv4.
LLD lifecycle и trigger scheduling с реальными agent data остаются частью VM acceptance.

## Grafana 12

`monitoring/grafana/package-hold.json`, provisioning provider и scrape example
проверены provisioning/API в Grafana 12.0.2 (12 panels). Datasource выбирается `$datasource`; inventory labels
job/env/group/instance фильтруют hosts, package_id — пакеты. env/group задавать
на targets; пример прилагается. Таблица — current held/unheld/unknown; persistent count отдельно.
Добавлена отдельная таблица persistent unheld с freshness/up guards. Scan age
не вычисляется для sentinel timestamp=0. Unknown считается отдельным check error,
не held и не unheld. Health не фильтруется
package_id, чтобы пустой пакетный набор не скрывал scanner failure.
