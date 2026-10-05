# Playbooks

`report.yml`: отдельные localhost init, managed hosts с serial и localhost finalize.
Для безопасных limits запускайте через `scripts/run-report.py`; localhost обязателен.
`exporter.yml`: state=present/absent lifecycle отдельного scanner/HTTP deployment.

Примеры всех режимов и privileges находятся в [README](../README.md).
