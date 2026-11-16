# Эксплуатация учебного VidHarbor

Проверено на облачном Docker/Compose, 2026-11-15. [Протокол](protocols/s05-s08-completion.md), [backup/restore](backup-restore.md). Production deployment, DNS/TLS, сервер и действующие n8n/Sheets в этой работе не изменялись.

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

S07 ещё не реализован: исходный согласованный `implementation-plan-s07.md` из 2d65cb9 ожидается от владельца. JSON diagnostics текущих компонентов и сохранённые ID не заменяют P06–P10, OpenTelemetry/Tempo, Prometheus/Grafana, ротацию и панель. Отдельные live/ready уже есть; полный мониторинг всех компонентов и alerts не заявлены.

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
