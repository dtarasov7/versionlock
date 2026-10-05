# Inventory и wire schemas

Нормативный JSON Schema Draft 2020-12: `schemas/inventory.schema.json`.
YAML после safe_load обязан образовать тот же JSON-совместимый объект. Другие inventory
vars не валидируются этой schema: передать wrapper только с package_hold_packages.
Duplicate YAML keys отвергать до JSON validation (обычный safe_load не всегда это делает).

```yaml
package_hold_packages:
  - id: java17
    names:
      redos: java-17-openjdk
      astra: openjdk-17-jre
      alt: java-17-openjdk
```

Все три ключа names обязательны, даже если текущая ОС только одна. null означает
«неприменимо» и явно разрешён, пустая строка запрещена. Отсутствующий ключ — ошибка,
а не skip. Names только exact binary package names, без architecture suffix/version/
regex/glob/provides. Поддерживаются ASCII буквы, цифры, _, ., +, -; первый символ
alphanumeric. Дистрибутив может накладывать более строгую grammar; adapter её проверяет.
ID `[a-z][a-z0-9_]{0,63}` стабилен во всех системах. Не использовать имя host/version в id.

Семантические ограничения сверх JSON Schema: id уникален; non-null names уникальны
внутри каждой OS mapping (нельзя двум id управлять одним package); максимум 1024
записи; все-null объект запрещён. Список [] допустим: CSV headers, health-only metrics,
LLD пустой, scan успешен. Ничего автоматически не разворачивается в зависимости.

Resolved input: `[{"id":"java17","name":"openjdk-17-jre"}]`, null mappings исключены.
Resolved список ещё может содержать неустановленные имена: их отбрасывает backend,
не controller. Filter один для всех ролей, exporter/module далее реализованы раздельно.
Exporter config соответствует `schemas/exporter-config.schema.json`:

```json
{"schema_version":1,"os":"astra","backend":"dpkg_selections",
 "packages":[{"id":"java17","name":"openjdk-17-jre"}],
 "scan_interval_seconds":300,"scan_timeout_seconds":60,"command_timeout_seconds":30,
 "listen_address":"127.0.0.1","listen_port":9817,
 "status_path":"/var/lib/package-hold-exporter/status.json"}
```

Все ключи обязательны, unknown keys запрещены. Числа не bool. Runtime constraints:
command_timeout<=scan_timeout<scan_interval, backend соответствует os. Default bind
loopback: для внешнего Prometheus задать адрес management interface, firewall/reverse
proxy отдельно. JSON формируется to_json, не ручной конкатенацией строк.

Module result: `schemas/module-result.schema.json`; cache: `schemas/status.schema.json`.
Schema version меняется при несовместимости; reader отклоняет неизвестную версию.
Semantic validation добавляет unique ids, соответствие config_digest, согласованность
state/error/streak, timestamps и принадлежность records текущему resolved inventory.
Смена mapping или интервала меняет digest и сбрасывает streak; stale snapshot не
используется для продолжения серии наблюдений. Config digest вычисляется по UTF-8
json.dumps(config, sort_keys=True, separators=(',', ':'), ensure_ascii=True), SHA256.

Module arguments: `schemas/module-args.schema.json`; defaults применяет Ansible argument_spec,
JSON Schema default — аннотация, а не автоматическое заполнение.
`backend` необязателен: модуль выбирает его по `os-release` managed host. Если передан,
он служит проверкой ожидаемого backend; несовпадение завершается ошибкой без mutation.
