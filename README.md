# VidHarbor

Учебный видеосервис для Homework 005. Автор загружает исходник, фоновый исполнитель готовит HLS, зритель открывает ролик без регистрации. Для записи сведений о публикации предусмотрены n8n и Google Таблица; пока проверен отдельный тестовый получатель.

**Стек:** Vue + TypeScript, Django + Django REST Framework, PostgreSQL, RabbitMQ, собственный Python worker, FFmpeg, HLS/hls.js, Nginx, n8n/Google Sheets, Docker Compose, OpenTelemetry, Prometheus, Grafana и Tempo. Базовые проверки Compose запускаются через GitHub Actions. Полный Django-набор и S01 работают в GitHub Actions; отдельный S07 workflow подготовлен, но ещё не опубликован.

**Текущее состояние (2026-11-17)** — база main 73db264 уже содержит принятый S05, локальный отправитель S06 и учебный backup/clean restore S08, включая исправление rollback удаления. В отдельной неопубликованной ветке S07 добавлены реальные JSON/correlation/OpenTelemetry→Tempo, Prometheus/Grafana, приватный доступ/retention/ротация и автоматические smoke-проверки. PostgreSQL Django 91 PASS, облачный end-to-end trace и живая панель проверены. GitHub CI этой ветки Not Run. Настоящие n8n/Google Sheets, публичный HTTPS/сервер, независимый backup carrier и приёмка Homework 005 ещё открыты.

[Общий протокол с логами](docs/homework-05/protocols/s05-s08-completion.md) · [Запуск в Linux/облаке и обновление](docs/homework-05/operations.md) · [Резервная копия и clean restore](docs/homework-05/backup-restore.md) · [Оставшиеся задачи](tasks.md).

Docker на ноутбуке не требуется. Compose и тесты можно запускать в облачном Linux либо GitHub-hosted Actions. Для нового облачного стенда используйте Linux-команды из operations; PowerShell ниже — дополнительный вариант локального запуска.

## Локальный запуск

Из корня репозитория в PowerShell 7 при запущенном Docker Desktop подготовьте отдельные локальные секреты. Команда сохраняет уже существующие файлы.

```powershell
$dir = Join-Path (Get-Location) 'secrets'
New-Item -ItemType Directory -Force $dir | Out-Null
foreach ($name in @('django_secret_key', 'postgres_password', 'rabbitmq_password')) {
    $path = Join-Path $dir $name
    if (-not (Test-Path -LiteralPath $path)) {
        $bytes = [byte[]]::new(32)
        [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
        [IO.File]::WriteAllText($path, [Convert]::ToHexString($bytes))
    }
}
docker compose --project-name vidharbor --env-file .env.example up --build -d --wait --wait-timeout 180
```

Откройте `http://127.0.0.1:8085/`. Здесь находится кабинет автора. Каталог доступен по `http://127.0.0.1:8085/catalog`. В [.env.example](.env.example) указаны порт, имена и путь к папке секретов. Сами файлы `secrets/` исключены из Git и передаются контейнерам через Compose. PostgreSQL и RabbitMQ доступны внутри сети проекта. Образы закреплены по digest, зависимости — в [requirements.txt](api/requirements.txt) и [package-lock.json](web/package-lock.json).

При первом запуске Compose применяет миграции PostgreSQL. Откройте экран входа, создайте аккаунт, войдите и выберите MP4, MOV, MKV или WebM. Лимит по умолчанию — 5 ГиБ. В кабинете видны ход передачи и состояние обработки. У готового видео появляется ссылка на просмотр. При обрыве начните передачу заново. Если пропал ответ на завершение, нажмите «Повторить проверку». После устранимой ошибки обработки доступен повтор из сохранённого оригинала, привязанный к failed_job_id. Удаление сразу закрывает новые запросы и затем очищает файлы; состояние DELETING означает, что cleanup ещё продолжается.

Оригиналы и временные части находятся в томе `media_data`, который не подключён к Nginx. Незавершённые части удаляются после 24 часов без активности. Для проверки граничных размеров на небольших образцах задайте `VIDHARBOR_MAX_UPLOAD_BYTES` на отдельном тестовом стенде.

| Адрес | Что проверяет | Ответ |
|---|---|---|
| `/health/live` | Django отвечает | `200`, `{"status":"live"}` |
| `/health/ready` | Django выполняет `SELECT 1` и открывает канал с авторизацией в RabbitMQ | `200` при доступных зависимостях, `503` с указанием недоступной зависимости при сбое |

```powershell
curl.exe -i http://127.0.0.1:8085/health/live
curl.exe -i http://127.0.0.1:8085/health/ready
```

Для абсолютной ссылки в событии `video.ready` можно задать `VIDHARBOR_PUBLIC_BASE_URL`. По умолчанию используется адрес локального Compose с выбранным портом. Таймаут FFmpeg задаёт `VIDHARBOR_FFMPEG_TIMEOUT` в секундах, по умолчанию `10800`.

Проверенные версии S01: Node.js 24.22.2, Vue 3.5.45, TypeScript 5.9.3, Vite 7.3.8, Nginx 1.30.6, Python 3.13.18, Django 5.2.19, Django REST Framework 3.16.1, PostgreSQL 16.14 и RabbitMQ 4.2.9.

Остановить локальный Compose с сохранением томов: `docker compose --project-name vidharbor --env-file .env.example down`.

| Артефакт | Вход / статус | Действие |
|---|---|---|
| [Product Spec](docs/product-spec.md) | В архиве версия 0.2 | Обновлена до 0.3 по DEC-003 |
| [Architecture](docs/homework-05/design.md) | Предыдущее предложение в текстовом виде | Подготовлен актуальный arc42 |
| [Decision log](docs/homework-05/decision-log.md) | DEC-001–003 в архиве | Сохранена история, добавлен DEC-004 |
| [Implementation Plan](docs/implementation-plan.md) | Готового плана в архиве не было | Draft для review |
| [Tasks](tasks.md) | Рабочего списка в архиве не было | Зависимости и ожидаемые результаты |
| [Traceability](docs/homework-05/requirements-traceability.md) | ID требований в локальных источниках | Связь решений, задач и проверок |
| [Reliability](docs/homework-05/reliability.md) | Решения и обсуждения в архиве | Draft контрактов и отказов |
| README и Git ignore | Входы без проектного репозитория | Базовые документы и исключения |
| [Production practice](docs/homework-05/production-practice.md), [testing report](docs/homework-05/testing-report.md), [backup/restore](docs/homework-05/backup-restore.md), [operations](docs/homework-05/operations.md) | Факты учебного стенда S05/S06/S08 | Внешняя интеграция, GitHub CI/Review S07 и production условия открыты |
| [Theory notes](docs/homework-05/theory-notes.md), Defense 005, Learning Pack 005 | 30 тем A/B/C подготовлены, понимание не проверено | Defense/ментор и KazanCourts отдельно |

Локальные входы: каноническое ДЗ №005 ред. 2, практическое ТЗ, AI Native Engineering, основной регламент ментора, Markdown roadmap, исторические Progress/Knowledge Gaps, Learning Pack Addendum, PRD 0.2, DEC-003 и прежнее предложение. В репозиторий входят проектные документы. Казанские корты и теория учитываются в общей работе отдельно.

## Наблюдаемость и подготовка внешнего демо

Опциональный профиль и команды — [operations](docs/homework-05/operations.md#наблюдаемость-s07). Создать **отдельно** `telemetry_metrics_token` и `grafana_admin_password` в private secrets directory, затем использовать `-f compose.yaml -f compose.observability.yaml --profile observability`. Обычный Compose продолжает работать без telemetry secrets. UI только loopback: Grafana13007, Tempo13207, Prometheus13907; не открывать эти порты наружу.

[S07: протокол и доказательства](docs/homework-05/protocols/s07-observability.md) · [Учебный n8n/Sheets: точный контракт и сценарии](docs/homework-05/integration-rehearsal.md) · [Offline HTTPS/серверный checklist](docs/homework-05/public-deployment.md) · [Выбор независимых копий/authority](docs/homework-05/backup-restore.md#независимый-носитель-подготовленные-варианты-не-внедрение).

Эти шаблоны не являются выполненным deployment, подключением Google или доказательством восстановления после потери хоста. Самостоятельные публикация/merge/deployment в текущей задаче не выполняются.

После Review f1f1a47: [три исправления S07 и фактические проверки](docs/homework-05/protocols/s07-review-fixes.md). Vue связывает передачу байтов и READY одним trace; SQL/Rabbit сбор метрик изолирован от HTTP API и ограничен deadline; CI reports перенесены в видимую s07-evidence/. Изменения ещё не закоммичены/не опубликованы, GitHub artifact availability Not Run.
