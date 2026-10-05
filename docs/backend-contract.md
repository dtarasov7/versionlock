# Backend contract v1

Два независимых потребителя: Ansible module (query/held/unheld) и exporter (query only).
Общие golden fixtures; запрещён импорт одной реализации другой. Состояние — наличие
действующего штатного hold, а не соответствие версии политике.

## Общие правила

1. Validate config целиком **до** определения установленных пакетов. Пропуск mapping — ошибка.
2. Получить batch inventory установленных пакетов, затем точное сопоставление NAME.
   Не использовать provides, shell glob, substring, зависимости или «похожее» имя.
3. Если snapshot inventory достоверен, отсутствующие пропускаются без пользовательской записи.
   Неуспешный inventory query => host-level error и packages=[], не отсутствие.
4. Query результатов hold только для установленного множества. Empty set — success даже
   при отсутствии hold plugin; prerequisites role отдельно сообщает о plugin.
5. Недоступный backend при непустом installed set даёт unknown для этих записей.
6. Перед mutation повторно проверить наличие; после — наличие и hold.
   Если пакет исчез — убрать row; если rpmdb/config изменились при чтении — повторить
   один раз в пределах deadline, затем concurrent_change. Это не transaction gate.
7. Root нужен для mutation и scanner service (единый доступ ко всем backend).
   Audit допустим без root при достаточных read permissions; EACCES не unheld.
8. subprocess argv, shell=False, абсолютные проверенные пути executables, LC_ALL=C,
   PATH из system dirs, очищенные APT_CONFIG/DPKG_ROOT/DPKG_ADMINDIR и подобные overrides.
   Имена проходят schema allowlist, не начинаются с '-', не содержат пробелов, glob,
   newline, '/', ':', кавычек. Аргументы команды не конструируются shell-строкой.
9. Query commands: rc=0 и строгий формат — success. В v1 batch inventory запросы не
   используют rc=1 как свидетельство отсутствия. Любой иной rc, timeout, сигнал,
   invalid UTF-8, превышение лимита, лишние/пропущенные поля => error.
   stderr с диагностикой базы/permission => error даже при rc=0; whitelist warnings
   добавляется только с fixture конкретного профиля. Empty hold list с rc=0 валиден.
10. Ошибки: configuration_invalid, unsupported_platform, prerequisite_missing,
    backend_disabled, permission_denied, command_failed, command_timeout,
    malformed_output, unsupported_rule, external_hold_conflict, concurrent_change,
    verification_failed, cache_io_error. message не становится metric label.

Unknown не исправляется попыткой «просто поставить hold». Audit не изменяет конфиги,
не обновляет repositories и не устанавливает пакеты. Пользовательский запрос unheld
не даёт права менять чужие пакетные ограничения с широкими шаблонами.

## RED OS: dnf_versionlock (DNF 4)

Prerequisites: rpm, dnf, python3-dnf-plugin-versionlock. Проверить global plugins=1,
plugin enabled=1, поддерживаемые default pluginpath/pluginconfpath, `[main] locklist`
в `/etc/dnf/plugins/versionlock.conf` и его читаемость. INI parser без interpolation,
строгая обработка bool, duplicate/нестандартные overrides => configuration error.
Путь locklist брать из config, а не всегда versionlock.list. DNF5 — отдельный будущий
backend; не трактовать как DNF4. Стандартный config profile должен быть записан fixtures.
Проверять владельца фактического executable: `rpm -qf <dnf-path> --qf '%{NAME}\t%{VERSION}\n'`
должен вернуть одну запись `dnf` версии 4.x. `dnf --version` не использовать в scanner:
он открывает `/var/log/hawkey.log` до применения CLI overrides и ломает read-only sandbox.
Для query `versionlock list` logdir/cachedir/persistdir направляются в отдельный
временный каталог, удаляемый после команды. Конфиги, locklist и rpmdb остаются штатными;
repo отключены. Mutation использует обычные DNF paths. Scratch writes не меняют
системные package/config/cache/log files; module и exporter реализуют это независимо.

Installed inventory:
`rpm -qa --qf '%{NAME}\t%{EPOCHNUM}\t%{VERSION}\t%{RELEASE}\t%{ARCH}\n'`.
Проверить rc и пять полей каждой строки, epoch numeric; старый rpm без EPOCHNUM —
unsupported capability. Все строки бинарных пакетов с NAME==resolved name включаются.
Единственное metadata-исключение: `gpg-pubkey`, epoch=0, hexadecimal version/release,
arch=`(none)` — импортированный RPM public key, не бинарный пакет; такая строка
исключается из inventory. Fixture получена из RED OS 7.3.6 Docker, rpm 4.18.2.
Другие невалидные arch не игнорируются. NEVRA живёт
только в памяти mutation; не сохраняется в cache/CSV/metrics.

Проверка: читать configured locklist как документ, игнорировать только пустые строки
и комментарии, распознавать записи с полным literal name, numeric epoch, literal
version/release, arch literal или `*`. Разбор справа (release/arch, version, epoch/name),
а не split('-') по всем дефисам имени. **Finite positive lock(s)** для exact name и
всех установленных arch => held при enabled plugin; отсутствие подходящих positive
rules => unheld. Запись на другую фиксированную версию всё ещё означает наличие
lock, а не version mismatch: этот продукт не сравнивает allowed и installed версии.
Внутренние NEVRA-токены нужны для распознавания lock, не для version policy.

Глобальные/именные wildcard rules, диапазоны `--raw`, строки `!` и неоднозначные
записи за пределами v1 grammar => unknown/unsupported_rule для затронутого имени;
если нельзя надёжно определить область действия — для всех installed monitored.
Даже `name-*` нельзя принимать за защиту: он допускает всё. Broad raw lock не
перезаписывать автоматически. `exclude=` DNF не является backend этого проекта.

Held mutation: из свежего rpm inventory получить конкретные NEVRA и передать
`dnf -q --disablerepo=* versionlock add <exact-NEVRA> ...` (каждый элемент argv).
Без `--raw`; без resolver fallback по одному имени; отсутствие installed => skip.
Так DNF выбирает только установленную NEVRA, не доступные repo versions.
На профиле необходимо доказать работу без enabled repos; иначе capability error,
не сетевой fallback. После rc=0 перечитать plugin config, locklist и installed inventory.

Unheld mutation: выбрать supported positive records с exact name и передать их
целиком в `dnf -q --disablerepo=* versionlock delete <record> ...`; literal `*` в
сохранённом arch заменить на `[*]` в аргументе matcher. Bare name запрещён: DNF
сопоставляет также NAME-VERSION, и запрос `tool-2` может удалить lock `tool` версии 2.
Удаляются все positive locks этого exact name (это явно запрошено), даже если
неполное покрытие arch уже даёт aggregate unheld. Сначала убедиться, что нет unsupported/conflicting
rules; не применять clear. Повторная проверка должна дать unheld. rc=0 сам по себе
не доказывает запись/удаление. Операции под собственным flock, с bounded timeout;
параллельный сторонний writer не обязан уважать flock, поэтому нужна проверка изменений.

Основания: [DNF versionlock manual](https://dnf-plugins-core.readthedocs.io/en/latest/versionlock.html)
описывает installed-first resolution, raw, locklist и отсутствие фильтрации
нетранзакционных запросов; [upstream implementation](https://raw.githubusercontent.com/rpm-software-management/dnf-plugins-core/master/plugins/versionlock.py)
показывает чтение locklist, delete и генерацию arch wildcard. В v1 сознательно ограничена
grammar, чтобы standard-library exporter не имитировал весь DNF solver.

## Astra: dpkg_selections

Prerequisites: dpkg-query, apt-mark (пакеты dpkg, apt). Backend независим от ALT.
Installed/hold batch query одним argv:
`dpkg-query -W -f='${Package}\t${Architecture}\t${Status}\n'`.
Кавычки здесь — обозначение одного аргумента; shell не запускается.
Status разбирается на want, error, actual. Для actual=installed и error=ok:
want=hold => held; want=install/deinstall/purge/unknown => unheld.
actual=config-files/not-installed => отсутствует, silently skip.
Half-installed, unpacked, half-configured, triggers-awaited/pending и reinstreq не
выдавать за чистое отсутствие: host-level query error, если установленность не
подтверждена; actual=installed с bad eflag => установленный unknown.
Неизвестные токены => malformed_output. Multiarch собирается по Package и Architecture.

Mutation: `apt-mark hold <name:arch> ...` / `apt-mark unhold <name:arch> ...` для всех
подтверждённо установленных arch; rc=0 только начало проверки, перечитать dpkg-query.
При partial multiarch hold aggregate state уже unheld, но explicit unhold всё равно
снимает оставшиеся held selections. Check mode показывает это изменение; postcondition
проверяет каждую архитектуру, а не только aggregate unheld.
`apt-mark showhold` — операторская диагностика; module/exporter используют Status,
чтобы различать residual config, установленность и selection в одном snapshot.
Не редактировать dpkg status напрямую, не делать dpkg --set-selections всего списка.

[apt-mark manual](https://manpages.debian.org/bookworm/apt/apt-mark.8.en.html):
hold запрещает автоматические install/upgrade/remove; unhold снимает selection.
[dpkg-query manual](https://manpages.debian.org/bookworm/dpkg/dpkg-query.1.en.html)
описывает machine-readable showformat, статусные поля и rc=0/1/2.
В нашем batch contract любой nonzero — ошибка; rc=1 не silently skip.

## ALT: alt_rpm_hold, provisional

Prerequisites: rpm, apt-config, apt-get из ALT apt; **не dpkg, не Debian apt-mark**.
Installed inventory: тот же rpm query, что RED OS. Проверка effective configuration:
`apt-config dump`, rc=0, parser строк `key "escaped value";`, поддержка повторяющихся
`RPM::Hold::` list entries. Не grep файла: include/порядок/clear могут менять effective value.
APT keys case-insensitive: `rpm::hold::` равнозначен `RPM::Hold::`. Именованные
прямые children (`RPM::Hold::admin "^bash$";`) также содержат действующие patterns.
Более глубокие вложенные children дают unknown/unsupported_rule, не unheld.
Строка `RPM::Hold "";` — контейнер списка, не правило «держать всё».
Native ALT p10 dump сохраняет одиночные backslash в regex значениях других keys,
например `Dir::Ignore-Files-Silently:: "\.bak$";`. Такие значения не считать
malformed и не преобразовывать неизвестные backslash sequences. Реальный dump
`molecule-alt:p10`, apt 0.5.15lorg2-alt95.p10.2, сохранён в
`tests/fixtures/alt-p10-apt-config.dump` и проверяется обеими implementations.

Существующие правила исполняются через glibc `regcomp`/`regexec` с флагами
`REG_EXTENDED | REG_ICASE | REG_NOSUB`, как в ALT APT `RPMPackageData`.
Matcher использует thread-local C locale, совпадающую с `LC_ALL=C` runner.
Поиск не требует полного совпадения: anchors определяет само правило; пустое
правило совпадает со всеми именами. Поддерживаются POSIX character classes,
alternation, repetitions, штатный `^(kernel|alsa)[0-9]+-source` и bare names.
Python `re` не используется для интерпретации hold regex. glibc требуется для
подтверждённого ABI `regex_t`; другая libc => `unsupported_platform`.
Невалидное выражение => conservative unknown/`configuration_invalid` для всех
monitored installed. APT предупреждает и игнорирует такое правило; adapter
отказывается заявлять достоверный hold при ошибке конфигурации.

`apt-config dump` выводит значения буквально: backslash sequences не декодировать
как JSON/APT escaping. Generated exact anchored pattern `^<escaped-name>$`
экранирует '.' как `[.]`, '+' как `[+]`, '-' вне class оставляет literal.
Пример `^libstdc[+][+]$`. Изменение hold удаляет только собственную canonical запись,
даже когда другое выражение совпадает с тем же пакетом.
Native p10 сравнение обоих adapters с установленным `HoldPackage` реализовано в
`tests/integration/alt-posix.py`; флаги подтверждены также
[исходниками ALT APT](https://git.altlinux.org/gears/a/apt.git?p=apt.git;a=blob;f=apt-pkg/rpm/rpmpackagedata.cc;hb=HEAD).

Hold: сохранять отдельный owned fragment
`/etc/apt/apt.conf.d/90-package-hold.conf` root:root 0644:

```text
RPM::Hold {
    "^nginx$";
};
```

Read-modify-write только собственных canonical exact entries; сохранить остальные regex entries
собственного файла даже если их больше нет в inventory (удаление inventory не unhold).
Не заменять файлы администратора. Atomic replace + fsync, затем apt-config dump и
повторная проверка. Unheld удаляет own exact entry. Если effective hold остался из
общего правила собственного или чужого файла, вернуть held + external_hold_conflict, failed=true; не заявлять успех
и не редактировать чужое regex. Unknown не мутировать. Пустой owned fragment удалить.
Parser обязан учитывать malformed config и загрузку directory из эффективного config;
нестандартный Dir::Etc::parts — configuration error v1.

Выбранный механизм подтверждён примерами ALT, но **семантика solver для современных
целевых релизов не доказана этим исследованием**. Список RPM::Hold в config — наблюдение
настройки, не доказательство того, что dependency update не обойдёт её.
До acceptance ниже backend диагностический/provisional. Подробности и источники:
[compatibility.md](compatibility.md). Не маскировать это свойство названием production-ready.

## Сопоставимость и postconditions

Каждый backend обязан пройти один набор semantic cases двумя независимыми adapters:
module query и exporter scan. Сравниваются только id, package, state, error code;
module-specific changed и scanner timestamps не сравниваются. Fixtures включают
stderr, rc, config files, installed inventory, expected rows. Synthetic fixtures
не объявляются записями реальных ОС. Mutation verification использует до/после snapshots,
а не строку «success» команды. Полная матрица в [testing.md](testing.md).

## Уточнения grammar и bounded I/O

DNF supported positive record: `NAME-EPOCH:VERSION-RELEASE.ARCH`. NAME — inventory
allowlist; EPOCH `[0-9]+`; VERSION и RELEASE `[A-Za-z0-9_+~^.]+` (без wildcard,
дефиса, пробела); ARCH `[A-Za-z0-9_]+` либо `*`. Разобрать arch по последней точке,
release по последнему дефису, затем epoch/name по разделителям `:` и последнему
дефису слева от epoch. Невалидная grammar => malformed_output; валидные DNF raw
patterns вне grammar => unsupported_rule. Нельзя молча игнорировать rule.
Дистрибутивные extension tokens требуют fixture и расширения grammar обеих реализаций.

Astra имя дополнительно соответствует `[a-z0-9][a-z0-9+.-]+`; architecture берётся
только из проверенного dpkg output, не из inventory. RPM inventory rows проверяются
до конструирования NEVRA. Command stdout cap 16 MiB, stderr cap 64 KiB; cache cap
4 MiB. Достигнутый cap — error, не усечённый «успешный» список. Read-only query
не должен запускать maintainer hooks/обновление repo. Timeout общий на scan плюс
индивидуальный на command; после deadline процесс terminate/kill и reap.
