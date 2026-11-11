# Протокол S01 · Docker Compose и проверки сервиса

10 ноября 2026 года, Windows 11 Pro, Docker Desktop с Linux Engine. Итог: T101, T102 и T104 выполнены. T103 проводилась отдельно до сборки приложения и записана в [протоколе RabbitMQ](s01-rabbitmq.md).

## T101

Docker Desktop был остановлен. После запуска Linux Engine ответил. Первый запрос запуска завершился таймаутом ожидания Desktop, а сам движок стал доступен позже. Использован уже скачанный Alpine. Пакеты для этой проверки не скачивались.

```powershell
docker desktop start --timeout 45
docker version --format 'Client={{.Client.Version}} Server={{if .Server}}{{.Server.Version}}{{end}}'
docker info --format 'OSType={{.OSType}} NCPU={{.NCPU}} MemTotal={{.MemTotal}} DockerRootDir={{.DockerRootDir}}'
docker compose version
docker run --rm --pull=never --network none --memory 64m --cpus 0.25 --label 'vidharbor.diagnostic=T101' alpine:3.20 sh -ec 'echo smoke_ok; uname -sm; grep -E "^(MemTotal|MemAvailable|SwapFree):" /proc/meminfo; if [ -r /sys/fs/cgroup/memory.max ]; then echo -n "Container memory limit bytes: "; cat /sys/fs/cgroup/memory.max; fi; df -Pk /'
```

| Замер | Факт |
|---|---|
| Docker Engine / Compose | 29.2.0 / v5.0.2 |
| Режим движка и тестовый контейнер | Linux x86_64, `smoke_ok`, код выхода `0`, контейнер удалён |
| Ресурсы Docker | 8 логических CPU, 8 160 247 808 байт RAM (7,60 ГиБ) |
| Доступная память при тесте | Linux 6,93 ГиБ, Windows 1,11 ГиБ после запуска Docker |
| Диск с данными Docker | Диск C:, свободно 22 587 961 344 байта (21,04 ГиБ), файл `docker_data.vhdx` занимает 21,09 ГиБ |
| Лимит тестового контейнера | 64 МиБ (67 108 864 байта по cgroup), 0,25 CPU |

Docker проверен для небольшого стенда с ограничениями ресурсов и малыми данными. Большой файл и восстановление требуют отдельного замера.

## T102

До успешного запуска было 0 контейнеров и 0 томов проекта VidHarbor. Compose создал сервисы `web`, `api`, `db` и `rabbitmq`, отдельную сеть и новые тома базы и очереди. При первой попытке проверка RabbitMQ от root создала служебный файл с неподходящим владельцем. После исправления проверки повторный старт с чистыми томами завершился успешно.

```powershell
docker compose --project-name vidharbor --env-file .env.example config -q
docker compose --project-name vidharbor --env-file .env.example up --build -d --wait --wait-timeout 180
docker compose --project-name vidharbor --env-file .env.example ps
```

Фактические версии: Node.js 24.22.2, Vue 3.5.45, TypeScript 5.9.3, Vite 7.3.8, Nginx 1.30.6, Python 3.13.18, Django 5.2.19, Django REST Framework 3.16.1, PostgreSQL 16.14 и RabbitMQ 4.2.9. Использован доступный образ PostgreSQL 16.14 после повторных таймаутов загрузки образа 17.

Образы закреплены по digest в [Compose](../../../compose.yaml) и Dockerfile [API](../../../api/Dockerfile) и [web](../../../web/Dockerfile). Зависимости закреплены в [requirements.txt](../../../api/requirements.txt) и [package-lock.json](../../../web/package-lock.json). Сборка Vue, проверка TypeScript и `python -m pip check` прошли. Подготовка локальных файлов секретов описана в [README](../../../README.md).

## T104

Страница `http://127.0.0.1:8085/` ответила HTTP `200`. Chrome 157.0.8103.39 в headless-режиме отрисовал Vue-заглушку «Основа VidHarbor запущена.». На хосте опубликован только веб-порт `127.0.0.1:8085`.

`live` проверял ответ Django. `ready` выполнял `SELECT 1` в PostgreSQL и открывал авторизованный канал AMQP в RabbitMQ. Проверяли ответы через PowerShell до, во время и после каждой остановки.

```powershell
Invoke-WebRequest -Uri 'http://127.0.0.1:8085/health/live' -TimeoutSec 8 -SkipHttpErrorCheck -NoProxy
Invoke-WebRequest -Uri 'http://127.0.0.1:8085/health/ready' -TimeoutSec 8 -SkipHttpErrorCheck -NoProxy

docker compose --project-name vidharbor --env-file .env.example stop --timeout 8 rabbitmq
docker compose --project-name vidharbor --env-file .env.example start rabbitmq
docker compose --project-name vidharbor --env-file .env.example stop --timeout 8 db
docker compose --project-name vidharbor --env-file .env.example start db
docker compose --project-name vidharbor --env-file .env.example exec -T api python -m pip check
```

| Состояние | `live` | `ready` |
|---|---|---|
| Чистый запуск | HTTP 200, `{"status":"live"}` | HTTP 200, `{"status":"ready","checks":{"postgres":true,"rabbitmq":true}}` |
| RabbitMQ остановлен | HTTP 200, `{"status":"live"}` | HTTP 503, `{"status":"not_ready","checks":{"postgres":true,"rabbitmq":false}}` |
| RabbitMQ восстановлен | HTTP 200, `{"status":"live"}` | HTTP 200, обе проверки `true` |
| PostgreSQL остановлен | HTTP 200, `{"status":"live"}` | HTTP 503, `{"status":"not_ready","checks":{"postgres":false,"rabbitmq":true}}` |
| PostgreSQL восстановлен | HTTP 200, `{"status":"live"}` | HTTP 200, обе проверки `true` |

[.env.example](../../../.env.example) содержит обычные настройки и путь к папке секретов. Все три локальных файла исключены из Git. Проверка файлов для коммита не обнаружила в них значения этих секретов. Обе зависимости восстановлены, локальный стенд оставлен запущенным.
