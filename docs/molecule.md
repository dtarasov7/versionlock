# Molecule на локальных native images

> Рабочий каталог `.build` с логами и test artifacts удалён после проверок.
> Упомянутые ниже пути описывают артефакты соответствующих прогонов.

> Ниже сохранены результаты предыдущего этапа. Актуальные результаты независимого
> review и обновлённых сценариев: [senior-review.md](senior-review.md).

Прогон 2026-10-03: controller Ansible 2.13.13, Python 3.8.20, Molecule 4.0.4.
Исходные images не изменялись; каждый сценарий создаёт и удаляет disposable container.

| Image | Python внутри image | prerequisites | control | exporter |
|---|---|---|---|---|
| `molecule-astra:1.7.10` | 3.7.3 | PASS | PASS | FAIL: требуется Python >=3.8 |
| `molecule-alt:p10` | 3.9.20 | PASS | PASS | PASS |
| `molecule_local/redos:7.3.6` | 3.8.20 | PASS (timeout 180s) | PASS (timeout 180s) | PASS |

PASS означает полный `molecule test`: dependency/destroy/syntax/create/converge/
idempotence/verify/destroy. Exporter scenario использует пустой inventory и проверяет
scanner/timer/cache/HTTP/check-mode removal/uninstall. Он не доказывает native hold
непустого набора или solver semantics.

Astra prerequisites/control действительно проверили native dpkg/apt-mark, held,
повторную idempotence и check-mode unhold. Managed Python 3.7.3 этого image отличается
от заявленной конфигурации Python 3.8: результат не является доказательством Astra/Python 3.8.
Для exporter нужен image с Python 3.8 либо установленный отдельный interpreter;
задайте его в `package_hold_exporter_python` и `ansible_python_interpreter`.

ALT имеет systemd в `/lib/systemd/systemd`, хотя `/sbin/init` отсутствует.
Путь исправлен во всех трёх Molecule-конфигах. Native apt-config dump выявил ошибку
в обработке single-backslash patterns `Dir::Ignore-Files-Silently`; исправлены оба
parser и добавлен regression fixture `tests/fixtures/alt-p10-apt-config.dump`.
После добавления glibc POSIX matcher полный повтор prerequisites/control прошёл
за 25.0s и 19.8s соответственно. Штатное
`RPM::Hold:: "^(kernel|alsa)[0-9]+-source";` сохранено; конфигурация image не очищалась
для получения PASS. Дополнительный `tests/integration/alt-posix.py` подтвердил
совпадение обеих implementations с установленным native `HoldPackage` на 14 regex
и 15 именах, late clear и настоящий hold/unhold/conflict на bash.
Production support этого профиля ещё требует solver acceptance.

RED OS image не содержит versionlock plugin. Первый download metadata занял 42s,
поэтому default command timeout 30s прервал remediation. В Molecule converge
prerequisites/control timeout увеличен до 180s; рабочий default роли остался 30s.
Оба повторных `molecule test` прошли: 137.5s и 124.4s соответственно.
Control подтвердил native DNF versionlock held/query, second converge changed=0
и check-mode unhold без снятия hold. Solver и настоящий delete требуют отдельного acceptance.

## Повторный запуск

Используйте controller venv с `requirements-dev.txt` на Python 3.8.
Из корня проекта:

```bash
project_root="$PWD"
for role in prerequisites control exporter; do
  (
    cd "roles/package_hold_$role"
    MOLECULE_IMAGE=molecule-alt:p10 \
    MOLECULE_EPHEMERAL_DIRECTORY="$project_root/.build/molecule/alt-$role" \
    XDG_CACHE_HOME="$project_root/.build/cache" \
    ANSIBLE_LOCAL_TEMP="$project_root/.build/ansible-tmp" \
    ANSIBLE_GALAXY_CACHE_DIR="$project_root/.build/galaxy-cache" \
    PYTHONPYCACHEPREFIX="$project_root/.build/pycache" \
    molecule test -s default
  )
done
```

Подставьте другой `MOLECULE_IMAGE` и label ephemeral directory для Astra/RED OS.
Запускайте OS последовательно: container names уникальны между ролями, но одинаковы
для разных OS одной роли. Ожидаемые отказы выше не следует скрывать skip/ignore.

Логи этого прогона: `.build/molecule-matrix/*.log`, повторные ALT/RED OS проверки:
`.build/molecule-alt-recheck/`, `.build/molecule-redos-recheck/`; в каждой директории
`results.json` содержит return codes и длительности. Все generated controller files
находятся в workspace. Не связанные с проектом контейнеры не изменялись.
