# VidHarbor

Учебный видеосервис для Homework 005. Автор загружает исходник, фоновый исполнитель готовит HLS, зритель открывает ролик без регистрации. n8n записывает сведения о публикации в Google Таблицу.

**Стек:** Vue + TypeScript, Django + Django REST Framework, PostgreSQL, RabbitMQ, собственный Python worker, FFmpeg, HLS/hls.js, Nginx, n8n/Google Sheets, Docker Compose, OpenTelemetry, Prometheus, Grafana и Tempo. Базовые проверки Compose запускаются через GitHub Actions. Полный набор CI запланирован на S07.

**Текущий этап** — S02. В локальном кабинете можно зарегистрироваться, войти и загрузить видео. После проверки оригинал сохраняется в закрытом томе, а задание и запись outbox — в PostgreSQL. Отправка из outbox начнётся на S03.

[Протокол локальных проверок S02](docs/homework-05/protocols/s02-auth-upload.md).

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

Откройте `http://127.0.0.1:8085/`. В [.env.example](.env.example) указаны порт, имена и путь к папке секретов. Сами файлы `secrets/` исключены из Git и передаются контейнерам через Compose. PostgreSQL и RabbitMQ доступны внутри сети проекта. Образы закреплены по digest, зависимости — в [requirements.txt](api/requirements.txt) и [package-lock.json](web/package-lock.json).

При первом запуске Compose применяет миграции PostgreSQL. Откройте экран входа, создайте аккаунт, войдите и выберите MP4, MOV, MKV или WebM. Лимит по умолчанию — 5 ГиБ. В кабинете видны ход передачи и статус «Ожидает обработки». При обрыве начните передачу заново. Если пропал ответ на завершение, нажмите «Повторить проверку».

Оригиналы и временные части находятся в томе `media_data`, который не подключён к Nginx. Незавершённые части удаляются после 24 часов без активности. Для проверки граничных размеров на небольших образцах задайте `VIDHARBOR_MAX_UPLOAD_BYTES` на отдельном тестовом стенде.

| Адрес | Что проверяет | Ответ |
|---|---|---|
| `/health/live` | Django отвечает | `200`, `{"status":"live"}` |
| `/health/ready` | Django выполняет `SELECT 1` и открывает канал с авторизацией в RabbitMQ | `200` при доступных зависимостях, `503` с указанием недоступной зависимости при сбое |

```powershell
curl.exe -i http://127.0.0.1:8085/health/live
curl.exe -i http://127.0.0.1:8085/health/ready
```

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
| production-practice, testing-report, backup-restore, operations | Доказательства ещё предстоит получить | В `docs/homework-05/` по итогам проверок |
| theory-notes, Defense 005, Learning Pack 005 | Общий учебный цикл | По регламенту и результатам приёмки |

Локальные входы: каноническое ДЗ №005 ред. 2, практическое ТЗ, AI Native Engineering, основной регламент ментора, Markdown roadmap, исторические Progress/Knowledge Gaps, Learning Pack Addendum, PRD 0.2, DEC-003 и прежнее предложение. В репозиторий входят проектные документы. Казанские корты и теория учитываются в общей работе отдельно.
