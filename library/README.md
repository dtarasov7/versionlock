# Modules

* `package_hold`: native query/held/unheld, installed-only results, check mode.
* `package_hold_prerequisite`: platform detection, capability audit/remediation.
* `package_hold_report`: controller-only init/finalize и atomic UTF-8 CSV.

Аргументы и result schemas: [inventory-schema](../docs/inventory-schema.md),
[backend-contract](../docs/backend-contract.md). Module helpers отделены от
single-file exporter: импорт и копирование runtime implementations запрещены.
