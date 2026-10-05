# Проверка имён пакетов

Проверено 4 октября 2026 года по **бинарным** индексам пакетов для x86_64/amd64
(включая noarch/all). Имена исходных пакетов и исполняемых файлов не подставлялись
вместо имён устанавливаемых RPM/DEB.

Область проверки: РЕД ОС 7.3 **стандартная редакция**, ALT 10 **p10**,
Astra Linux **Special Edition 1.7**, текущий `stable` (Release базового репозитория:
`1.7.8-base`). Это не проверка РЕД ОС certified, ALT c10f2, всех frozen-срезов
Astra 1.7 или сторонних репозиториев производителей ПО.

[package_hold_packages.yml](package_hold_packages.yml) содержит подтверждённые
имена. `null` означает, что соответствующий пакет не найден в проверенных индексах
и на этой ОС исключён из hold. Это не означает, что продукт невозможно установить.
Позиции без найденного пакета во всех трёх ОС оставлены только в таблице:
схема проекта запрещает активные записи с тремя `null`.

| ПО | РЕД ОС 7.3 | Astra SE 1.7 | ALT p10 | Примечание |
| --- | --- | --- | --- | --- |
| nginx | `nginx` | `nginx` | `nginx` |  |
| nginx core | `nginx-core` | `nginx-core` | Не найден | РЕД ОС: пакет исполняемого файла nginx; Astra: дополнительный метапакет стандартных модулей. |
| Angie | `angie` | Не найден | Не найден |  |
| HAProxy | `haproxy` | `haproxy` | `haproxy` |  |
| Grafana | `grafana` | `grafana` | `grafana` |  |
| Prometheus 2.x | `golang-github-prometheus-2` | Не найден | `prometheus` | РЕД ОС: 2.53.5; ALT: 2.51.1. В Astra сервер не найден. |
| OpenSearch | `opensearch` | Не найден | `opensearch` |  |
| OpenSearch Dashboards | `opensearch-dashboards` | Не найден | Не найден |  |
| Redis | `redis` | `redis-server` | `redis` |  |
| Kafka | `apache-kafka` | Не найден | `kafka` |  |
| ZooKeeper | `zookeeper` | Не найден | Не найден |  |
| ZooKeeper Java | `zookeeper-java` | Не найден | Не найден | РЕД ОС: JAR-компоненты сервера, дополнение к zookeeper. |
| Alertmanager | `golang-github-prometheus-alertmanager` | `prometheus-alertmanager` | `prometheus-alertmanager` |  |
| VictoriaMetrics | `victoria-metrics-singlenode` | Не найден | `victoriametrics` | Выбран single-node. Другие варианты перечислены ниже. |
| exporter-exporter | `exporter_exporter` | Не найден | Не найден |  |
| process-exporter | `golang-github-ncabatoff-process-exporter` | Не найден | Не найден |  |
| node-exporter | `prometheus-node_exporter` | `prometheus-node-exporter` | `prometheus-node_exporter` |  |
| elasticsearch-exporter | Не найден | Не найден | Не найден |  |
| kafka-exporter | `golang-github-danielqsj-kafka-exporter` | Не найден | Не найден | В os также есть старый kafka-exporter 1.4.2; добавлен отдельной записью. |
| kubeadm | `kubernetes-kubeadm` | `kubernetes-admin` | `kubernetes1.28-kubeadm` | ALT: пример ветки 1.28. |
| kubelet | `kubernetes-node` | `kubernetes-node` | `kubernetes1.28-kubelet` | ALT: пример ветки 1.28. |
| kubectl | `kubernetes-client` | `kubernetes-client` | `kubernetes1.28-client` | ALT: пример ветки 1.28. |
| containerd | `containerd` | `containerd` | `containerd` |  |
| crictl | `cri-tools` | `cri-tools` | `cri-tools1.28` | ALT: пример ветки 1.28. |
| Docker Engine | `docker-ce` | `docker.io` | `docker-engine` |  |
| Docker CLI | `docker-ce-cli` | В составе `docker.io` | `docker-cli` | Astra: CLI входит в docker.io, отдельная запись не нужна. |
| Jaeger | Не найден | Не найден | Не найден | Библиотеки Jaeger в РЕД ОС не являются сервером Jaeger. |
| OpenTelemetry Collector | `opentelemetry-collector-contrib` | Не найден | Не найден | В РЕД ОС подтверждён вариант contrib. |
| Data Prepper | Не найден | Не найден | Не найден |  |
| PostgreSQL | `postgresql-server` | `postgresql-11` | `postgresql13-server` | Выбраны пакеты серверов, а не метапакет postgresql. Другие major перечислены ниже. |
| Postgres Pro | Не найден | Не найден | Не найден | Нужны репозиторий поставщика, редакция Standard/Enterprise и major; прежнее предположение Standard 17 удалено. |
| Consul | `consul` | Не найден | `consul` |  |
| etcd | `etcd` | `etcd-server` | `etcd` |  |
| Vault | `vault` | Не найден | `vault` |  |
| GitLab | `gitlab-ce` | Не найден | Не найден | РЕД ОС: отдельный 3rdparty/gitlab. gitlab-runner не заменяет сервер GitLab. |
| Nexus | `nexus` | Не найден | Не найден | РЕД ОС: отдельный 3rdparty/nexus. |
| Keepalived | `keepalived` | `keepalived` | `keepalived` |  |
| ClickHouse Server | `clickhouse-server` | Не найден | `clickhouse-server` | РЕД ОС: отдельный 3rdparty/clickhouse. |
| ClickHouse Client | `clickhouse-client` | Не найден | `clickhouse-client` | РЕД ОС: отдельный 3rdparty/clickhouse. |
| ClickHouse Common | `clickhouse-common-static` | Не найден | `clickhouse-common-static` | РЕД ОС: отдельный 3rdparty/clickhouse. |
| kafka-exporter (старый пакет) | `kafka-exporter` | Не найден | Не найден | РЕД ОС: os, версия 1.4.2. |

## Версии и варианты

- **nginx в РЕД ОС:** `nginx` содержит службу и вспомогательные скрипты,
  `/usr/sbin/nginx` находится в `nginx-core`; оба имени включены в YAML.
  В текущем extended Astra бинарный сервер находится в `nginx`, а `nginx-core`
  является дополнительным метапакетом стандартных модулей (также включён).
- **ZooKeeper в РЕД ОС:** `zookeeper` содержит `zkServer.sh` и службу,
  `zookeeper-java` — JAR-файлы; оба пакета включены. Проверено по RPM-заголовкам.
- **Prometheus 2.x в РЕД ОС:** заголовок RPM
  [golang-github-prometheus-2-2.53.5-1.el7.x86_64.rpm](http://repo.red-soft.ru/redos/7.3/x86_64/updates/golang-github-prometheus-2-2.53.5-1.el7.x86_64.rpm)
  подтверждает `Name: golang-github-prometheus-2`, `Version: 2.53.5`,
  файлы `/usr/bin/prometheus` и `/usr/bin/promtool`. Именно **дефис** перед `2`;
  имя `golang-github-prometheus_2` в проверенных индексах не найдено.
  В `os` прежнее имя `golang-github-prometheus` ещё встречается с версией 2.49.1,
  но в `updates` то же имя используется для 3.x (есть 3.6.0).
  Поэтому оно не включено в пример для 2.x.
- **Prometheus в ALT p10:** `prometheus` имеет версию 2.51.1.
  В проверенных индексах Astra 1.7 найдены exporter'ы и Alertmanager,
  но пакет сервера Prometheus не найден; нельзя переносить его наличие из Debian
  или Astra 1.8 в mapping для 1.7.
- **Kubernetes/CRI в ALT p10:** подтверждены ветки 1.25, 1.26, 1.27, 1.28.
  В YAML выбрана 1.28. Для другой установленной ветки одновременно замените
  `kubernetes1.28-kubeadm`, `kubernetes1.28-kubelet`, `kubernetes1.28-client`
  и `cri-tools1.28`. Список не выбирает версию Kubernetes для установки.
  Принадлежность `/usr/bin/kubeadm`, `/usr/bin/kubelet`, `/usr/bin/kubectl`
  и `/usr/bin/crictl` проверена по файловым спискам RPM-заголовков p10.
- **Kubernetes в РЕД ОС/Astra:** заголовки RPM РЕД ОС подтверждают kubeadm в
  `kubernetes-kubeadm`, kubelet в `kubernetes-node`, kubectl в `kubernetes-client`.
  Содержимое DEB Astra `kubernetes-admin_1.23.17-astra1_amd64.deb` подтверждает
  `/usr/bin/kubeadm`; описание `kubernetes-node` явно указывает kubelet/kube-proxy,
  описание `kubernetes-client` — kubectl.
- **PostgreSQL:** для РЕД ОС выбран `postgresql-server` из базовых репозиториев
  (ветка 12 в updates), для Astra — `postgresql-11`, для ALT — `postgresql13-server`.
  В РЕД ОС также найдены `postgresql10-server` и `postgresql13-server`–`postgresql18-server`,
  в ALT — `postgresql13-server`–`postgresql17-server`. Для другого установленного
  major замените соответствующее имя. Astra `postgresql` — метапакет, зависящий
  от `postgresql-11`; удерживать нужно сам пакет сервера.
- **VictoriaMetrics:** РЕД ОС содержит `victoria-metrics`,
  `victoria-metrics-singlenode`, `victoria-metrics-cluster`; выбран single-node,
  наличие `/usr/bin/victoria-metrics` подтверждено RPM-заголовком.
  В ALT single-node — `victoriametrics`; cluster разнесён на
  `victoriametrics-cluster-vminsert`, `victoriametrics-cluster-vmselect`,
  `victoriametrics-cluster-vmstorage`. Для cluster нужны отдельные записи компонентов.
- **Kafka exporter в РЕД ОС:** в `os` найден `kafka-exporter` 1.4.2,
  в `updates` — `golang-github-danielqsj-kafka-exporter` 1.9.0.
  Оба имени включены отдельными ID: hold применяется только к установленным пакетам.
- **GitLab/Nexus/ClickHouse в РЕД ОС:** подтверждены в опубликованных РЕД СОФТ
  каталогах `3rdparty`, отдельно от `os/updates`; соответствующие репозитории
  должны быть подключены. Сервер GitLab — `gitlab-ce`; `gitlab-runner` является
  другим продуктом. В ALT найденные ClickHouse-пакеты входят непосредственно в p10.
- **Не подставлены похожие продукты:** `angie-pro-repo` в ALT только подключает
  репозиторий и не является Angie; `vaultwarden` не является HashiCorp Vault;
  Go-библиотеки Jaeger не являются сервером Jaeger; `opensearch-prometheus-exporter-plugin`
  не является standalone elasticsearch-exporter; пакет `docker` в Astra не является
  контейнерным движком, для него используется `docker.io`.
- **Postgres Pro:** в проверенных индексах ОС не найден. Имена пакетов из репозитория
  поставщика зависят от редакции и major; автоматически выбирать Standard 17 оснований нет.

## Источники проверки

РЕД ОС: скачаны `repomd.xml` и указанные в них `primary.xml.gz`; SHA-256 сжатых
индексов совпали с метаданными. Для Astra скачаны все 12 `Packages.gz`
(main/contrib/non-free для каждого из четырёх репозиториев), SHA-256 проверены
по `Release`. Для ALT разобраны RPM-заголовки из обоих `pkglist.classic.xz`.
Пакеты не устанавливались, состояние систем не изменялось.

| ОС | Проверенные индексы/каталоги |
| --- | --- |
| РЕД ОС 7.3 | [os/repodata](http://repo.red-soft.ru/redos/7.3/x86_64/os/repodata/repomd.xml), [updates/repodata](http://repo.red-soft.ru/redos/7.3/x86_64/updates/repodata/repomd.xml), [extras/postgresql14-73](http://repo.red-soft.ru/redos/7.3/x86_64/extras/postgresql14-73/repodata/repomd.xml) |
| РЕД ОС 7.3, 3rdparty | [gitlab](http://repo.red-soft.ru/redos/7.3/x86_64/3rdparty/gitlab/repodata/repomd.xml), [nexus](http://repo.red-soft.ru/redos/7.3/x86_64/3rdparty/nexus/repodata/repomd.xml), [clickhouse](http://repo.red-soft.ru/redos/7.3/x86_64/3rdparty/clickhouse/repodata/repomd.xml) |
| ALT p10 | [x86_64 pkglist](https://mirror.yandex.ru/altlinux/p10/branch/x86_64/base/pkglist.classic.xz), [noarch pkglist](https://mirror.yandex.ru/altlinux/p10/branch/noarch/base/pkglist.classic.xz) |
| Astra 1.7, зеркало | [repository-main](https://mirror.yandex.ru/mirrors/astralinux/stable/1.7_x86-64/repository-main/dists/1.7_x86-64/Release), [repository-base](https://mirror.yandex.ru/mirrors/astralinux/stable/1.7_x86-64/repository-base/dists/1.7_x86-64/Release), [repository-extended](https://mirror.yandex.ru/mirrors/astralinux/stable/1.7_x86-64/repository-extended/dists/1.7_x86-64/Release), [repository-update](https://mirror.yandex.ru/mirrors/astralinux/stable/1.7_x86-64/repository-update/dists/1.7_x86-64/Release) |

Официальный каталог РЕД СОФТ по HTTPS не ответил из среды проверки;
использован опубликованный каталог того же сервера по HTTP. Официальный сервер
Astra возвращал HTTP 403, ALT — страницу проверки браузера;
для индексов этих ОС использовано зеркало Yandex. Доступность дополнительных
закрытых и сторонних репозиториев этой проверкой не охвачена.
