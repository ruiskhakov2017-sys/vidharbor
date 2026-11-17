# Эксплуатация учебного VidHarbor

S05–S08 опубликованы в main 73db264; S07 дополнен и проверен в отдельной облачной ветке, 2026-11-16. [Протокол](protocols/s05-s08-completion.md), [backup/restore](backup-restore.md). Production deployment, DNS/TLS, сервер и действующие n8n/Sheets в этой работе не изменялись.

## Запуск

Нужны Linux Docker Engine и Compose v2, свободное место на Docker data-root, действующие CPU/RAM limits для проверки бюджетов. Ноутбук и Docker Desktop не требуются: команды можно выполнять в облачном стенде или GitHub-hosted Actions. Каждая параллельная работа получает отдельные project name, порт и тома. Не запускать общий Compose с именем чужого стенда.

На новом стенде из корня нужной рабочей ветки:

```bash
export COMPOSE_PROJECT_NAME=vidharbor-demo
export VIDHARBOR_PORT=8181
export VIDHARBOR_PUBLIC_BASE_URL=http://127.0.0.1:8181
export VIDHARBOR_SECRETS_DIR=/workspace/private-demo/secrets
umask 077
mkdir -p "$VIDHARBOR_SECRETS_DIR"
for name in django_secret_key postgres_password rabbitmq_password; do
  if [ ! -e "$VIDHARBOR_SECRETS_DIR/$name" ]; then
    openssl rand -hex 32 > "$VIDHARBOR_SECRETS_DIR/$name"
    chmod 0444 "$VIDHARBOR_SECRETS_DIR/$name"
  fi
done
docker version
docker compose version
docker compose --env-file .env.example config --quiet
docker compose --env-file .env.example up --build -d --wait --wait-timeout 240
docker compose --env-file .env.example ps
curl --fail http://127.0.0.1:8181/health/live
curl --fail http://127.0.0.1:8181/health/ready
```

Секреты нельзя печатать в журнал, заменять при повторном запуске или хранить в Git. В управляемом облаке могут понадобиться отдельные override для доверенного proxy CA, прав build context и healthcheck без системного proxy. В выполненном прогоне они находились вне Git; точный список сохранён в протоколе. Стандартный Compose без этих override должен дополнительно пройти GitHub Actions после разрешения на публикацию.

Миграции применяет одноразовый `migrate`; затем api/upload, broker, relay, worker и cleanup запускаются автоматически. Notifier по умолчанию выключен. Создать автора через UI, загрузить допустимый исходник, дождаться READY, открыть `/catalog` и публичную страницу. Проверить Auto/ручные качества и сохранённый SHA-256 оригинала. Синтетический прогон 3.53 ГБ прошёл; длительный произвольный пользовательский файл требует отдельного бюджета, а не обещания времени по одному 40-секундному образцу.

## Наблюдение и управление

```bash
docker compose --env-file .env.example logs --no-color --tail 100 api upload relay worker cleanup
docker compose --env-file .env.example exec -T upload python manage.py showmigrations vidharbor
docker compose --env-file .env.example exec -T upload python manage.py makemigrations --check --dry-run
docker compose --env-file .env.example run --rm --no-deps upload python manage.py test vidharbor --noinput
```

Тесты создают отдельную test DB; для регрессии с отключением broker/БД использовать отдельный тестовый project. S01 и S02 smoke меняют состояние зависимостей/очереди, их нельзя направлять на действующий demo с работающим worker без предусмотренной изоляции. Текущий `smoke_s02.py` требует работающий relay и остановленный worker: сверяет одну Job/Outbox, исходные ID/хэш, реальный persistent payload в RabbitMQ и подтверждённый sent_at; сообщения возвращаются nack/requeue. Историческое ожидание неотправленной Outbox устранено, поскольку оно конфликтовало с relay. После сценария восстановить только первоначально работающие сервисы. [Команды регрессии](protocols/s05-s08-completion.md#проверки-и-воспроизведение).

S07 реализован отдельно по явному заданию P06–P10: JSON/correlation, реальные OTel/Tempo traces, Prometheus/Grafana и ограниченное хранение. [Фактические проверки](protocols/s07-observability.md); исходный план 2d65cb9 не найден и ещё требует сверки. Публичная эксплуатация и GitHub CI новой ветки Not Run.

Повтор обработки: UI привязывает запрос к конкретному failed_job_id. При потере ответа повторить тот же запрос; не создавать новый цикл для другого задания автоматически. Timeout ffprobe отдаёт временную ошибку 503 с Retry-After, не расходуя generation. Исправить причину FFmpeg/места и повторить из сохранённого оригинала. Не редактировать Job/Attempt вручную для сброса истории.

Удаление сначала закрывает доступ и фиксирует intent в независимом от БД журнале; 202 означает ожидающую очистку, 200 — её завершение. При занятом flock или сбое cleanup доступ остаётся закрытым. Проверить cleanup log и повторный проход; не удалять lock-файл и не возвращать READY вручную. Загруженные ранее клиентом байты отозвать невозможно; гарантия относится к новым запросам. Историческая строка получателя сохраняется с уже недоступной ссылкой.

## Обновление

До обновления: закрепить исходный и целевой SHA/образы; сохранить текущие Compose/env/secret references; сделать согласованную проверенную копию и свежий журнал, проверить место. Остановить старые writers. На **копии** выполнить миграции и сверить старые данные, затем Django/regression. В этом задании реальная проверка 0003 → 0006 сохранила Upload/Video/Job/Attempt/Outbox и уникальность retry successor. Данные действующего main не менялись.

После разрешения на обновление нужного стенда:

```bash
docker compose --env-file .env.example build
docker compose --env-file .env.example run --rm --no-deps migrate
docker compose --env-file .env.example up -d --wait --wait-timeout 240
```

Перед этим требуется правильный checkout вне незавершённого merge; команды не выполняют Git pull/reset/merge автоматически. После миграций не откатывать бинарники поверх несовместимой схемы. Безопасный возврат при ошибке — проверенная копия в новом закрытом project, актуальный журнал и процедура clean restore, затем отдельное разрешение на переключение. Автоматическое обратное применение миграций на заполненной БД не проверено и не рекомендуется.

## Восстановление и остановка

Полная процедура, проверенные guards и результаты — в [backup-restore.md](backup-restore.md). Сохранить домен/абсолютные ссылки, fence старые writers с отключением их Docker restart policy, использовать новые тома, применить свежие удаления и reservation high-watermark. HTTP открывать после verification, notifier отдельно после внешней сверки. В текущем облачном demo восстановленный target обслуживает старый loopback-порт 8113; прежний source остаётся остановленным.

Обычная остановка сохраняет данные:

```bash
docker compose --env-file .env.example down
```

`down --volumes` разрешён только для заведомо одноразового тестового/CI project после сохранения доказательств; на demo и восстановленных данных не применять. В GitHub Actions always-cleanup удаляет только созданный job-ом стек и его тома. История незавершённого S06 merge в другом worktree не является частью эксплуатационной процедуры и не меняется.

## Наблюдаемость S07

Обычный `compose.yaml` не включает exporter/collector; JSON логи и Docker json-file rotation1m×3 включены без дополнительных сервисов. Для opt-in на **отдельном учебном** project сохранить существующие секреты и создать только отсутствующие `telemetry_metrics_token`/`grafana_admin_password` (случайные≥32 байт) по той же процедуре выше. Не печатать значения. В managed cloud public config bind mounts должны читаться service UID, secret directory остаётся0700.

```bash
# Из корня repo; project/port/secrets уже выбраны для этого стенда.
docker compose --env-file .env.example -f compose.yaml -f compose.observability.yaml --profile observability config --quiet
# Запуск только своего согласованного тестового стенда:
docker compose --env-file .env.example -f compose.yaml -f compose.observability.yaml --profile observability up --build -d --wait --wait-timeout 240
```

Grafana `http://127.0.0.1:13007`, login admin с private file; provisioned dashboard UID `vidharbor-overview`. Prometheus13907 и Tempo13207 не имеют внешней авторизации и потому доступны **только loopback/доверенным операторам**. Для удалённого просмотра — SSH tunnel, не `0.0.0.0`. OTLP4318 и component9100 не публикуются на host. `/internal/metrics` доступен Prometheus по Docker DNS с отдельным bearer; web возвращает404. Все клиенты той же Docker-сети считаются доверенной инфраструктурой; сетевая мультитенантность не реализована.

Prometheus: шесть targets(api/upload/relay/worker/cleanup и отдельный metrics collector), scrape5 с, retention6h/128MB. Queue depth — **ready messages, без unacked**; это не полный backlog, consumers отдельным gauge. Job/attempt/delivery history берётся из PostgreSQL отдельным collector-процессом, поэтому перезапуск процесса не обнуляет durable состояния; restore может уменьшить counters. HTTP/export metrics процессные и сбрасываются при restart; `rate()` учитывает reset. Notifier выключен по умолчанию; dashboard webhook metrics отражает Delivery в БД и тестового получателя, не настоящую Google Таблицу.

Tempo: retention1h, trace≤5MB, ingest1MB/s burst2MB; это rate/retention, не жёсткая quota filesystem. Compaction/WAL/block deletion асинхронны. На отдельном хосте нужны disk monitoring и запас; при большом трафике100% sampling/учебные limits пересмотреть до public deployment. Prometheus retention size также не включает все WAL/head overhead. Docker logs1m×3 на каждый контейнер, не общая гарантия размера data-root. Grafana SQLite хранит private preferences; автоматический TTL для её БД не заявлен, доступ ограничен. В панели запреты anonymous/signup/analytics/external plugin download.

Компоненты relay/worker/cleanup имеют внутренний `/health/live`200 для живого HTTP server и `/health/ready`200 лишь при свежем положительном tick (<10 с), иначе503. Docker health ориентируется на live, а readiness проверяется отдельно/метрикой; broker outage не должен вызывать бесконечный restart исправного процесса. API health сохраняет прежнюю семантику. Notifier internal checks включаются только при разрешённом notifications profile.

Минимальные alerts: collection unavailable15 с, target down30с, dependency unavailable15 с, due outbox age>120с1мин, webhook failed/blocked30с. Это учебные пороги; **не согласованные SLO и не внешнее оповещение**. Alertmanager/email/мессенджеры не подключались. Реальный broker alert дошёл до firing, затем данные/готовность восстановились.

По trace ID искать разрешённые поля JSON:

```bash
docker compose --env-file .env.example logs --no-color --no-log-prefix api upload relay worker | jq -c 'select(.trace_id == "82f77f97135deec8813a64d7c8bab838")'
```

Пример ID относится к сохранённому evidence; живой Tempo удалит его после retention. HTTP response выдаёт X-Request-ID/X-Correlation-ID/traceparent. Correlation=Upload UUID; request_id уникален на HTTP запрос, trace сохраняется через durable carrier/повторы. Содержание title/body/headers/cookies/query/exception text не логируется; диагностика ошибок — event/error_code/ID и span status. OTLP отправляется асинхронно, очередь256/batch32, HTTP timeout1с; отказ collector не блокирует бизнес-READY, но потерянные spans не воспроизводятся задним числом.

## Автоматические проверки и подготовленные подключения

`.github/workflows/django-tests.yml` и `s01-ci.yml` сохранены. Новый `s07-observability.yml`: GitHub-hosted ubuntu24.04, временные секреты, настоящий PG/Rabbit/FFmpeg/Tempo/Prometheus/Grafana, маленький синтетический fixture, trace/log/metrics/panel/outage/rotation, always-cleanup containers+volumes+secrets. Artifact проходит allowlist/credential scan и имеет учебный retention3дня; это не backup retention. Никаких n8n/Google credentials в jobs. Непрошедшая проверка даёт failed job; compileall/actionlint не заменяют реальные smoke.

Локальный запуск S07 smoke допустим только с `COMPOSE_PROJECT_NAME=vidharbor-s07-*`, отдельными данными и включённым profile. `scripts/smoke_s07.py --monitoring --fixture <маленький валидный файл> --report <private evidence path>` реально отключает Tempo/Prometheus/Rabbit, создаёт отдельные fixture-данные и терминальную ошибку, возвращая original fixture в finally. Не направлять на чужой/production project. Browser/rotation scripts требуют того же project. Workflow-equivalent выполнен в Codex; **GitHub-hosted run новой ветки Not Run**, push/PR запрещены. Старые зелёные Actions не относятся к новому SHA.

Дальнейшая [n8n/Sheets репетиция](integration-rehearsal.md), [public HTTPS/topology/update/rollback checklist](public-deployment.md) и [независимые копии](backup-restore.md#независимый-носитель-подготовленные-варианты-не-внедрение) подготовлены offline. Notifier/public gates и старые source restart-policy guards не обходить. Без внешнего согласования ничего не включать.

## Исправления S07 после Review f1f1a47

[Протокол исправлений](protocols/s07-review-fixes.md). API `/internal/metrics` отдаёт только процессные counters/histograms: SQL и RabbitMQ там не вызываются. В опциональном профиле отдельный `metrics` сервис (тот же app image,256 MiB/1 CPU, без host ports/media/journal mounts) собирает durable gauges. Prometheus обращается к его `/metrics` с тем же private bearer. Стандартный Compose не добавляет этот сервис.

Сборщик запускается дочерним процессом: общий wall timeout2 с, после SIGKILL максимум0.5 с на reap; один незавершённый child не порождает новые. В редком kernel-uninterruptible случае sampling прекращается, collection_up=0 и требуется restart сервиса; HTTP exporter продолжает отвечать. На каждый SQL statement250 мс, lock100 мс, сессия read-only; connect_timeout2 с также покрыт общим wall deadline. Интервал5 с после окончания предыдущего сбора. Только успешно проверенный Prometheus snapshot≤128KiB отдаётся до15 с; после failed/stale collection прошлые business gauges не выдаются. `vidharbor_metrics_collection_up`, snapshot_age, duration и counters с причинами timeout/collector_error показывают отказ; добавлено локальное alert rule. Показатели БД остаются приблизительным снимком, не новой бизнес-транзакцией.

PUT ответа сервера передаёт traceparent; Vue сохраняет его вместе с pending upload в памяти и отправляет в POST complete, включая повтор проверки. `upload.receive` измеряет чтение/запись/fsync исходных байтов; HTTP PUT включает весь серверный приём, клиентский wall замер дополнительно включает сеть. Это не новый body log и не замер чистой пропускной способности канала. После перезагрузки страницы in-memory pending context не восстанавливается; существующий бизнес-процесс загрузки не изменён. После создания Job carrier по-прежнему durable в Outbox/повторах. Новых миграций нет.

Workflow использует видимую `s07-evidence/`: все шесть report paths, проверка безопасности и upload-artifact смотрят туда же. Добавлены настоящий Vue upload→READY и тест pg_sleep(30) с параллельными catalogue/scrape. Доступность GitHub artifact пока **Not Run**, как и сам новый CI-run: ветка с исправлениями не опубликована. Не включать hidden-file upload для обхода проверки секретов.

Публикация PR #11 выявила отличие реального GitHub cgroup от прежнего cloud: при0.5CPU collector не укладывался в2с (timeout9, CPU throttling, OOM0; direct diagnostic6.3с, PG/Rabbit доступны). Для текущего профиля выделен1CPU; дочерний collect_metrics пропускает повторные Django URL/system checks. Полные checks остаются в startup/Django CI. Deadline2с, SQL250мс, stale/error guards и catalogue latency assertions сохранены. Реальные результаты нового прогона фиксируются в PR, не переносятся из старого cloud PASS.

Второй ограничитель внутри disposable collector отводит RabbitMQ observation0.5с целиком (DNS/TCP/AMQP RPC/close). Socket timeout сам по себе не ограничивает DNS и все passive RPC. При превышении сохраняются PostgreSQL gauges и выдаётся rabbitmq backend_up=0, очередь не подменяется фиктивным нулём. Вспомогательный daemon заканчивается вместе с дочерним процессом; в API/долгоживущем exporter нет таких потоков. Общий deadline2с сохраняется. Это исправлено после run44092292816, который уже прошёл нормальный сбор, но выявил отсутствие backend-down sample при остановленном broker.
