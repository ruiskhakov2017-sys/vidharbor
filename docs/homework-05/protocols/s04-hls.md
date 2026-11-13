# S04 · Протокол обработки и просмотра

Дата прогона — 13 ноября 2026 года, московское время. Рабочая ветка `feature/s04-hls` от `f120ac2`. В журналах контейнеров время указано в UTC.

## Среда

- Основной Compose `vidharbor-s04-check` на `127.0.0.1:8098`. PostgreSQL 16.14, RabbitMQ 4.2.9, FFmpeg и ffprobe 8.1.3, Nginx 1.30.6, hls.js 1.7.4. Worker запущен штатной командой `python manage.py transcode_videos`.
- Тест отказов использует `vidharbor-s04-faults` на `127.0.0.1:8099` с теми же образами, отдельной базой `vidharbor_s04_test` и отдельными томами. Сценарии внедряют сбои через тестовый процесс и не включаются в штатном worker.
- Проверка обновления `vidharbor-s04-upgrade` выполнена на копии остановленного тома S03. Исходный том был подключён только для чтения.
- Docker Desktop запустил Linux-контейнеры. Доступно 8 логических CPU и 8 160 243 712 байт памяти. Свободно 33 765 007 360 байт на диске C:. `df` внутри контейнера показал 934,1 ГиБ свободного адресного пространства виртуального диска. Для worker в Compose выставлены 2 CPU и 1024 МиБ.

## Команды

Для основного прогона использованы локальные тестовые секреты S02. Следующие команды запускаются из корня репозитория. Путь к секретам указывает на существующие тестовые файлы.

```powershell
$env:COMPOSE_PROJECT_NAME = 'vidharbor-s04-check'
$env:VIDHARBOR_DB_NAME = 'vidharbor_s04_test'
$env:VIDHARBOR_PORT = '8098'
$env:VIDHARBOR_SECRETS_DIR = (Join-Path $PWD 'test-results/s02/secrets')
docker compose --env-file .env.example up --build -d --wait --wait-timeout 300
docker compose --env-file .env.example exec -T worker python manage.py makemigrations --check --dry-run
docker compose --env-file .env.example run --rm --no-deps -T upload python manage.py test vidharbor --noinput -v 2
python scripts/smoke_s04.py test-results/s04 --state test-results/s04/browser-jobs.json
```

Исходники сгенерированы локально. Для Firefox использована настольная сборка Playwright, установленная командой `python -m playwright install firefox`. Chrome и Edge запущены из установленных каналов.

```powershell
$fixtures = Join-Path $PWD 'test-results/s04'
New-Item -ItemType Directory -Path $fixtures -Force | Out-Null
ffmpeg -nostdin -hide_banner -loglevel error -y -f lavfi -i testsrc2=size=1280x720:rate=12 -f lavfi -i sine=frequency=523:sample_rate=48000 -t 14 -c:v libx264 -preset ultrafast -threads:v 2 -pix_fmt yuv420p -c:a aac (Join-Path $fixtures 'landscape.mp4')
ffmpeg -nostdin -hide_banner -loglevel error -y -display_rotation 90 -i (Join-Path $fixtures 'landscape.mp4') -t 6 -map 0:v:0 -c copy (Join-Path $fixtures 'rotated.mov')
ffmpeg -nostdin -hide_banner -loglevel error -y -f lavfi -i testsrc2=size=320x180:rate=12 -t 8 -c:v libvpx-vp9 -deadline realtime -cpu-used 8 -an (Join-Path $fixtures 'small.webm')
ffmpeg -nostdin -hide_banner -loglevel error -y -i (Join-Path $fixtures 'landscape.mp4') -t 6 -c copy (Join-Path $fixtures 'sample.mkv')
```

Для стенда отказов использованы текущие образы основного проекта через Compose override с `image` для `web`, `api`, `upload`, `migrate` и `worker`. Сервисы запущены командой `up --no-build -d --wait web`. RabbitMQ получил временную политику для `video.jobs` с таймаутом подтверждения 60 секунд и теми же настройками DLQ. В штатной конфигурации осталось 4 часа.

```powershell
$env:COMPOSE_PROJECT_NAME = 'vidharbor-s04-faults'
$env:VIDHARBOR_PORT = '8099'
$override = Join-Path $fixtures 'faults-compose.yaml'
$services = @('web', 'api', 'upload', 'migrate', 'worker')
$lines = @('services:') + @($services | ForEach-Object { "  $($_):"; "    image: vidharbor-s04-check-$($_):latest" })
[IO.File]::WriteAllLines($override, [string[]]$lines)
docker compose --env-file .env.example -f compose.yaml -f $override up --no-build -d --wait --wait-timeout 300 web
$policy = '{"dead-letter-exchange":"video.dead","dead-letter-routing-key":"video.malformed.v1","dead-letter-strategy":"at-least-once","overflow":"reject-publish","consumer-timeout":60000}'
docker compose --env-file .env.example -f compose.yaml -f $override exec -T rabbitmq rabbitmqctl set_policy --apply-to quorum_queues --priority 1 s04-ack '^video[.]jobs$' $policy
$script = Join-Path $PWD 'scripts/smoke_s04_faults.py'
docker compose --env-file .env.example -f compose.yaml -f $override run --rm --no-deps -T --volume "$($fixtures):/fixtures:ro" --volume "$($script):/app/smoke_s04_faults.py:ro" worker python -u /app/smoke_s04_faults.py --ack-timeout
```

Сценарии и версии источников описаны в [плане S04](../../implementation-plan-s04.md). Формат события и граница доставки заданы в [схеме надёжности](../reliability.md#событие-публикации-s04).

## T401 и T403 · FFmpeg, каталог и HTTP

Первый MP4 загружен через форму в Chrome. MOV, WebM и MKV переданы через API из той же сессии. В PostgreSQL все четыре задания завершились `SUCCEEDED`, видео получили `READY`. Для каждого SHA-256 исходного файла, закрытого оригинала и записи загрузки совпали.

| Образец | Входные потоки | Фактические размеры вариантов | Выход |
|---|---|---|---|
| `landscape.mp4` | H.264 + AAC, 1280×720 | 640×360, 852×480, 1280×720 | H.264 + AAC |
| `rotated.mov` | H.264, 1280×720, метаданные поворота 90° | 360×640, 480×852, 720×1280 | H.264 |
| `small.webm` | VP9, 320×180 | 320×180 | H.264 |
| `sample.mkv` | H.264 + AAC, 1280×720 | 640×360, 852×480, 1280×720 | H.264 + AAC |

Промежуточные размеры округлены вниз до чётных пикселей при вписывании в рамку. Портретный образец соответствует [DEC-005](../decision-log.md#dec-005--вертикальная-рамка-и-событие-в-s04). В каждой выдаче один `master.m3u8`. Проверены `RESOLUTION`, завершённые плейлисты, HTTP `200` для всех указанных сегментов и фактические размеры и кодеки по `ffprobe`.

Гость нашёл ролик по названию с изменённым регистром, открыл страницу из каталога и по прямому адресу. Master и плейлисты отданы как `application/vnd.apple.mpegurl`, сегменты — `video/mp2t`. В ответах `no-store`. Для внутреннего маршрута Nginx, оригинала, каталога `processing` и неактивного поколения получен `404`. Прямой путь неготового видео проверен на стенде отказов.

```text
landscape.mp4 SHA-256=77e47493a5f2660b8693a02cd95ca4c6b13ddf8f5990857ccc08e4e8bc96d643
rotated.mov SHA-256=20cd425c256b70ee986542cc45fc820aafb57e75703fbb20840540974df7f13d
small.webm SHA-256=d9484614a32788e879c222bf5e9355f6719f4b7591465e2e4a320216105936d0
sample.mkv SHA-256=c0ee80c75746a6344ef157d19ba9a13182263bc3c4dc6e857111071366ea9ada
```

## Миграции и тесты Django

Миграции `0001`–`0003` применились на пустой базе. `makemigrations --check --dry-run` вернул `No changes detected`. На копии базы S03 миграция `0003` сохранила обе записи `QUEUED`, их ID и отметки отправки outbox. Поля опубликованного поколения и времени готовности остались пустыми.

```text
2587f3f5-c86f-445c-9528-90ae284f457b|QUEUED|225866f1-5aee-48b3-94cb-c062cfae8267|t
6d10139f-e6b1-4884-83f3-106fbfc3e69b|QUEUED|6a468b8c-ac05-46d9-bd1d-e4864337756f|t
Ran 9 tests in 60.724s
OK
```

Первый Chrome-прогон выявил сброс новой формы загрузки при завершении входа. Формы получили разные ключи Vue. Повторная загрузка прошла. После остальных трёх загрузок через API тест обновляет открытый кабинет, чтобы считать список из базы.

## T404 · Настольные браузеры

| Браузер | Версия | Проверено |
|---|---|---|
| Chrome | 157.0.8103.51 | Поиск без входа, прямая ссылка, Auto и ручной выбор всех вариантов |
| Edge | 157.0.4341.46 | Поиск без входа, прямая ссылка, Auto и ручной выбор всех вариантов |
| Firefox (сборка Playwright) | 148.0.2 | Поиск без входа, прямая ссылка, Auto и ручной выбор всех вариантов |

В каждом браузере проверены три образца. После каждого ручного переключения тест дождался изменения `videoWidth` и `videoHeight` до ожидаемых значений и движения времени воспроизведения. Проверен старт в Auto и возврат в Auto после ручного выбора. На малом WebM доступен один фактический вариант 320×180, на повёрнутом MOV — три портретных.

## T402 · Публикация и восстановление

| Сценарий | Что наблюдали |
|---|---|
| `SIGKILL` после переименования до коммита, затем `SIGKILL` после коммита до `ack` | До коммита страница, плейлисты и сегменты вернули `404`, записи `video.ready` в базе не было. Повтор с командой `326cf964-083c-49ee-8a6c-0ff7551580a2` принял прежний manifest. После второго падения дубль подтверждён, очередь пуста. Одна успешная попытка и событие `c722dc02-2727-4650-858e-aab84eda9f98`. SHA-256 manifest и оригинала сохранились. Копия файлов в неактивном поколении также закрыта. |
| Удалён сегмент из каталога, переименованного перед коммитом | Гость получил `404`. По manifest worker отверг результат, первая попытка `RETRY / worker_lost`. После повтора получены `READY` и `SUCCEEDED`. Исходная команда `929a5934-2068-4ee0-b5e5-320fa20bdcfd`. |
| `pg_terminate_backend` между переименованием и записью события | После закрытия сессии PostgreSQL незакоммиченный результат оставался закрыт. Worker с PID 156 переподключился, повторно получил команду `1b18d1a4-b550-4efb-b6d4-cf4b7a1310b2` и принял прежние файлы. Одна успешная попытка и событие `356b6bba-1133-49ba-b377-abc5a3ca87ff`. |
| Родитель worker убит при живом FFmpeg | FFmpeg сохранил `flock`. При повторе команды `6ac5c466-d821-47b1-a337-0b4b0fd659cb` новый worker ждал, число попыток оставалось 1, объём файлов вырос с 9 104 276 до 10 377 600 байт. После остановки старой группы повтор дал `RETRY / worker_lost` и `SUCCEEDED`. Каталог во время работы вернул `200` за 0,202 с. |
| RabbitMQ закрыл канал активной доставки | Пассивный запрос отсутствующей очереди вызвал `404 NOT_FOUND` в 21:33:01.952779 UTC. Worker остановил старый FFmpeg, сохранил `RETRY / channel_lost`, затем завершил задание второй попыткой. Исходная команда `3a5da2df-ec98-4c1c-a84b-0eee4bcbaf60`. |
| Истёк короткий бюджет FFmpeg | При бюджете 2 секунды одна попытка завершилась `ffmpeg_timeout`. Страница и медиа вернули `404`, события готовности не было. Команда `780fc6e6-c847-42d6-b89c-db512b8514c2`. |
| Таймаут подтверждения RabbitMQ при живом соединении | В 21:37:00.892376 UTC RabbitMQ записал precondition_failed при тестовом таймауте 60 000 мс. Через 98,6 с наблюдалась повторная доставка команды `d8f50998-3513-4224-a648-1c847d444c5b`. Тот же event_id 81d71c82-738e-455a-bc4e-f69a70785dcc и одна успешная попытка. После подтверждения очередь пуста. |
| Повреждённый исходник на тестовом томе | Одна попытка `FAILED / invalid_video`, страница и HLS закрыты. В RabbitMQ получено окончательное событие DLQ `171d6e90-d92e-41b7-96d4-ca2d2dafa457`. |

Для успешных заданий сверены `READY`, номер опубликованного поколения, manifest и запись `video.ready`. В ней `attempt_no=0`, `sent_at` пуст, `message_id` совпадает с `event_id`, а время совпадает с `Video.ready_at`. URL master абсолютный и отдаёт `200`. Ручной `ack` после коммита подтверждён тестом с повторной доставкой и контрольным чтением пустой очереди.

## Регрессия запуска и зависимостей

Проверен штатный запуск Compose и загрузка через Chrome. При отдельных остановках RabbitMQ и PostgreSQL `live` оставался `200`, `ready` возвращал `503` с соответствующей недоступной зависимостью. После каждого запуска `ready` вернулся к `200`.

```text
Ordinary Compose: live=200, ready=200 {'postgres': True, 'rabbitmq': True}
rabbitmq stopped: live=200, ready=503 {'postgres': True, 'rabbitmq': False}
rabbitmq restarted: live=200, ready=200 {'postgres': True, 'rabbitmq': True}
db stopped: live=200, ready=503 {'postgres': False, 'rabbitmq': True}
db restarted: live=200, ready=200 {'postgres': True, 'rabbitmq': True}
```

В кабинете автора API вернул READY со ссылкой, а для окончательной ошибки — FAILED без ссылки.

Все контейнеры тестовых проектов S04 остановлены. Тома сохранены для проверки.
