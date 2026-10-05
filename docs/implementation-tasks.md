# Реализация 0.2.0

Выполнены: custom module query/held/unheld, независимый single-file exporter,
общий inventory filter, prerequisites/control/exporter roles, argument_specs,
CSV controller init/finalize, timer/oneshot/HTTP services, cache-only Zabbix,
Grafana JSON, Prometheus rules, fixture/runtime/integration tests.

Проверки по исходному заданию: ansible-core 2.13.x, Python 3.8.
Это конфигурация проверки; верхнее ограничение версии Ansible не задано.
Фактические результаты проверок находятся в [verification.md](verification.md).

Для production acceptance остаются проверки на целевых VM, перечисленные в
[compatibility.md](compatibility.md): offline DNF add/delete и solver, Astra multiarch,
ALT p11 effective config/POSIX patterns и p10/p11 solver, MAC и systemd hardening
с непустым inventory на остальных VM profiles. Непустой scanner под sandbox проверен
в RED OS 7.3.6 и ALT p10 containers; детали — [senior-review.md](senior-review.md).
p10 POSIX matcher/merge/clear и hold/unhold проверены native tests.
Это внешние acceptance gates, а не отсутствующие runtime реализации.
