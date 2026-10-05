# Совместимость и доказательства

Проверено по доступным первичным источникам 2026-10-03. Реальные целевые VM не
запускались. «Кандидат» не означает tested/supported. Версии из исходных prompt2/3
не являются выполненными acceptance tests; текущий этап ограничен pasted request.
Native Docker images проверены Molecule отдельно от VM acceptance:
[результаты и ограничения](molecule.md). В Astra image Python 3.7.3, в ALT — 3.9.20;
они не подтверждают заявленную конфигурацию managed hosts с Python 3.8.

| ОС / кандидат | Backend | Основание | До production |
|---|---|---|---|
| RED OS 7.3, 8; DNF4 | dnf_versionlock | Официальная инструкция RED OS 7.3 + upstream DNF4 | Проверить 8 отдельно, plugin/config profile, offline add/delete, rpm EPOCHNUM |
| Astra Linux SE 1.7, 1.8 | dpkg_selections | Документация Astra 1.7 + dpkg/apt upstream | Проверить обе редакции, multiarch и системный Python |
| ALT p10/p11 products | alt_rpm_hold | Пример p10, RPM::Hold в документации поставщика, известные bugs | Обязательный solver test, effective config grammar и default regex |

OS detection: `/etc/os-release` читается как данные, не source/shell; ID `redos`,
`astra`, `altlinux` нормализуется в redos/astra/alt; неизвестные ID не угадываются по
наличию apt или ID_LIKE=debian. Роли используют Ansible fact
`ansible_facts.package_hold_platform`, который platform module заново получает из
строго разобранного os-release; exporter читает os-release независимо. VERSION_ID обязательный для профиля. В Astra уточнять
release facts, если VERSION_ID недостаточен для редакции. Новые ID допускаются после
fixture реального os-release. DNF5/YUM, Debian/Ubuntu и другие RPM distros вне v1.

Модули и scanner рассчитаны на Python 3.8+; системный interpreter задаётся ролью.
Проверенная конфигурация controller: ansible-core 2.13.13 и Python 3.8.20.
Это описание выполненных проверок. В metadata ролей минимальная версия — 2.13;
верхнего ограничения в коде нет. ansible-core 2.14, 2.16 и новее отдельно не проверялись.
Закреплённые версии requirements нужны для воспроизводимости проверок; при запуске
ролей установленный Ansible не заменяется и эти файлы автоматически не читаются.

Python должен одновременно подходить проекту и выбранному ansible-core. Например,
controller ansible-core 2.14 требует Python 3.9–3.11, а 2.16 — Python 3.10–3.12.
Python 3.8 на managed hosts входит в матрицу обеих этих версий. Для более новых
релизов проверяйте controller и target Python отдельно по
[официальной матрице Ansible](https://docs.ansible.com/projects/ansible/latest/reference_appendices/release_and_maintenance.html#ansible-core-support-matrix).
Systemd, read permissions, SELinux/Astra MAC проверяются на VM.
Не отключать MAC ради exporter: дать узкие разрешения или вернуть prerequisite error.

## Подтверждённые источники

* [RED OS: блокировка обновлений](https://redos.red-soft.ru/base/redos-7_3/7_3-base-consept/7_3-sys-dnf/7_3-dnf-automatic/):
  пакет python3-dnf-plugin-versionlock и команды add/list/delete.
* [DNF4 versionlock](https://dnf-plugins-core.readthedocs.io/en/latest/versionlock.html):
  installed-first resolution и ограничения non-transactional commands. Репозиторий
  [upstream plugin](https://raw.githubusercontent.com/rpm-software-management/dnf-plugins-core/master/plugins/versionlock.py)
  исследован как reference, не обещание идентичности дистрибутивного patchset.
* [Astra SE 1.7: руководство](https://wiki.astralinux.ru/spaces/flyingpdf/pdfpageexport.action?pageId=153485970):
  пример apt-mark hold postgresql-11. Точная структура dpkg query опирается на
  [dpkg-query](https://manpages.debian.org/bookworm/dpkg/dpkg-query.1.en.html),
  команды изменения — на [apt-mark](https://manpages.debian.org/bookworm/apt/apt-mark.8.en.html).
* [BaseALT: интеграционная инструкция](https://www.basealt.ru/fileadmin/user_upload/compatibility/instr/51f7a3df9cbf054dd9b0ff5d59014c31.pdf):
  пример RPM::Hold в apt.conf.d (старый p9, не доказательство p11).
* [ALT bug 47565](https://bugzilla.altlinux.org/show_bug.cgi?id=47565):
  применение RPM::Hold при переходе на p10 и результат dist-upgrade.
* [ALT bug 22805](https://bugzilla.altlinux.org/show_bug.cgi?id=22805):
  сообщения об обходе hold при обновлении зависимостей; исторические данные,
  не доказательство исправления или сохранения бага на p11.

## ALT: что известно и что неизвестно

ALT APT работает с RPM. Выбран отдельный backend RPM::Hold, запись в owned fragment,
чтение через apt-config dump. Поддержка apt-mark hold на современных ALT этим
исследованием **не установлена**; команды Debian переносить нельзя.
Исходники `RPMPackageData` ALT APT подтверждают POSIX ERE с `REG_ICASE` и
`REG_NOSUB`. Оба matcher сравнены напрямую с `HoldPackage` установленной библиотеки
apt 0.5.15lorg2-alt95.p10.2 в `molecule-alt:p10`: штатное kernel/alsa правило,
POSIX classes, регистр, substring matching, empty regex, backslashes и merge/clear.
Также проверены native hold/unhold, idempotence, check mode и broad-rule conflict.
p11 и остальные patchsets ещё требуют отдельной проверки.

Главный открытый вопрос: является ли RPM::Hold непреодолимым для обычного dist-upgrade
при изменении зависимостей. Если solver его обходит, требование гарантированной
защиты на этом профиле невыполнимо выбранным механизмом. Результат не исправлять
переименованием metric, pinning версии или требованием менять процесс обновлений.
Зафиксировать unsupported profile; искать другой штатный backend отдельным решением.

Стандартные kernel regex поддерживаются glibc matcher. Невалидные regex дают
unknown/configuration_invalid; общие правила сохраняются при изменении конкретного
пакета. Остающийся hold после удаления own exact entry => external_hold_conflict.

## Конкретный acceptance на одноразовой VM

1. Сохранить os-release, `rpm -q apt rpm` (ALT), `dnf --version` и rpm plugin package
   (RED OS), `dpkg-query -W apt dpkg` (Astra), man pages и effective config.
2. Поднять локальный тестовый repo с toy-a v1/v2 и toy-b, которому требуется toy-a v2.
   Это fixture версий пакетного менеджера; продукт не начинает мониторить версии.
3. Установить toy-a v1; выполнить query, held, query, held повторно, check_mode unheld.
4. На ALT сохранить `apt-config dump` до/после fragment; проверить две независимые
   RPM::Hold секции, default kernel regex, escaping `+`/`.` и поздний clear override.
5. Выполнить simulation обычного upgrade/dist-upgrade, затем реальную транзакцию на
   snapshot VM, включая toy-b dependency. Проверить, что toy-a не обновлён.
   RED OS — dnf upgrade, Astra/ALT — apt-get upgrade и dist-upgrade. Никаких bypass flags.
6. Explicit unheld -> update -> held; повторить с несколькими arch, удалением пакета,
   plugin disabled, unreadable config, malformed output и конкурирующей транзакцией.
7. Проверить, что scanner/HTTP/agent работают под финальными правами; no network
   metadata refresh в scan; сохранить fixtures и точные package-manager builds.
8. Только после этого добавить tested profile. Не устанавливать эти тестовые пакеты
   на production host. В рамках текущей работы VM отсутствуют, результаты не выдуманы.

Обновлённые native и systemd проверки после hardening: [senior-review.md](senior-review.md).
