# Changelog

## Unreleased — 2026-10-04

- Термин Baseline заменён на «проверенная конфигурация» в пользовательской документации.
  Удалено ошибочное «Ansible 2.13.x (не выше)»: верхний предел не задан, новые версии
  отдельно не проверялись. Уточнены требования Python выбранного ansible-core.

- Модуль `package_hold` автоматически выбирает backend по ОС. Параметр `backend`
  необязателен и сохранён как проверка ожидаемого backend; роль не передаёт его.

- Удалены рабочие test artifacts `.build`; обычный запуск использует стандартные
  temporary paths Ansible. README и примеры предполагают уже установленный controller.

- Удалена упаковка Ansible collection: `galaxy.yml`, `meta/runtime.yml`, namespace
  в вызовах ролей/модулей и symlink bootstrap для тестов больше не нужны.
- Модули находятся в `library/`, helpers — в `module_utils/`, фильтр mapping —
  в `filter_plugins/`. Локальные пути настроены в `ansible.cfg`.
- Обновлены playbooks, примеры, Molecule и документация; добавлены controller
  `requirements.txt` и пример прямого использования трёх ролей.
- Wrapper отчётов выбирает конфигурацию проекта при запуске из другого каталога.
- Поведение hold, exporter, CSV и мониторинга сохранено. При обновлении deployment
  используйте короткие имена ролей/модулей и новые пути; установка collection не нужна.

## 0.2.0 — 2026-10-03

- Независимый review/hardening: исправлены DNF lock collision, ALT named/case-insensitive
  holds, DNF read-only query под systemd sandbox, отчёты при недоступных targets,
  partial multiarch unhold, Zabbix discovery/UUID и ограничения HTTP clients.
- Добавлены регрессионные и нативные проверки, persistent table Grafana и подробный
  [отчёт о проверках и ограничениях](docs/senior-review.md).
