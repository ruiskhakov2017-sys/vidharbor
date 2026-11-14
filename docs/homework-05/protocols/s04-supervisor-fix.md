# S04: исправление supervisor и регрессия orphan/flock

Статус: локальное исправление, подготовлено для независимого Review; разрешения на Git-публикацию нет. Исходный опубликованный `main`: `1d609a710a7ba3c68d12b6d830d21b2344e3a7ca`. Рабочая ветка: `feature/s04-supervisor-fix`, отдельный worktree `/workspace/vidharbor-s04-supervisor-fix`. Дата проверок: 2026-11-13. S05, S06, S07 и S08 в этом проходе не изменяются.

## Причина и доказательства до исправления

В закреплённом Alpine-образе `/usr/bin/timeout` разрешался в `/bin/busybox`, пакет `busybox-1.37.0-r31`. Это **не GNU timeout**. Переданный через `pass_fds` дескриптор lock-файла наследуют и FFmpeg, и отделившийся watchdog BusyBox. Watchdog живёт в собственной группе/сессии; остановка группы, известной Python worker, его не останавливает.

Свежая диагностика воспроизвела исходный сценарий из неизменённого `scripts/smoke_s04_faults.py` с настоящими PostgreSQL, RabbitMQ и FFmpeg. Контроллер использовал его `make_job`, `workers_for`, fault-режим `orphan`, проверки HTTP и механизм восстановления. Полный исходный smoke до исправления заново не запускался; для baseline выполнен именно диагностический сценарий orphan.

| Момент / процесс | PID | PPID | PGID | SID | Дескриптор / результат |
|---|---:|---:|---:|---:|---|
| Worker до сбоя | 11 | 1 | 1 | 1 | fd 8, `FLOCK WRITE`, inode 1878246 |
| Настоящий FFmpeg до сбоя | 15 | 11 | 15 | 15 | тот же унаследованный fd 8 и lock |
| BusyBox watchdog | 17 | 1 | 17 | 17 | тот же fd 8 и lock, другая группа |
| FFmpeg после SIGKILL worker | 15 | 1 | 15 | 15 | пишет; внешний претендент получает EWOULDBLOCK |
| Новый worker | 49 | 1 | 1 | 1 | fd 8 открыт, **без** записи `lock:` в fdinfo; ожидает |
| После `killpg(15, SIGKILL)` | 17 | 1 | 17 | 17 | FFmpeg отсутствует, watchdog жив и удерживает lock |

Между снимками при живом осиротевшем FFmpeg объём сегментов вырос с 221464 до 2647040 байт; после остановки группы остался 2647040. Вторая попытка не начиналась, HTTP выдача была закрыта. После остановки группы независимый `flock(LOCK_EX | LOCK_NB)` всё ещё отклонялся. `/proc/17/fdinfo/8` содержал `FLOCK ADVISORY WRITE` на inode 1878246; у ожидающего worker этого признака не было.

`flock` относится к общему open file description, поэтому нельзя считать любой процесс с открытым lock-файлом владельцем. После смерти исходного держателя `/proc/locks` в данном PID namespace уже не показывал запись, а `fdinfo` у наследников показывал PID 0. Владение подтверждалось одновременно fdinfo и реальной неуспешной попыткой получить lock. В JSON сохранены PID/PPID/PGID/SID, executable, командные строки, соответствующие FD, fdinfo, inode и размеры; отношения PPID задают дерево процессов.

Причина установлена в выборе реализации supervisor в образе. Проверка корректно обнаруживала оставшийся lock. Ослабление harness, удаление lock-файла или изменение ожидания не требуется. Утверждения старых протоколов о PASS orphan не заменяют эту воспроизводимую диагностику текущего закреплённого образа.

## Минимальное исправление

В target `upload` файла `api/Dockerfile` добавлен закреплённый `coreutils=9.11-r0`; версия FFmpeg остаётся `8.1.3-r0`. Alpine v3.24 предоставляет GNU timeout в пакете coreutils, отдельного пакета coreutils-timeout в проверенном индексе нет. Подмена busybox-applet происходит штатно при установке пакета. Проверено: `timeout (GNU coreutils) 9.11`, executable `/bin/coreutils`.

Вызов `timeout -k 5 <FFMPEG_TIMEOUT>`, `start_new_session=True`, `pass_fds`, `stop_group`, предел 10800 секунд, grace 5 секунд, AMQP I/O, Outbox, правила попыток и схема БД не изменены. Полный coreutils добавляет утилиты и зависимости в upload/worker; app target их не получает. Самостоятельный Python supervisor и новый сервис не вводятся.

`scripts/smoke_s04_supervisor.py` добавляет три проверки настоящих Linux-процессов и flock на временных файлах. Проверка явным образом отклоняет образ без GNU timeout. В этих трёх проверках писатель — отдельный Python-процесс, намеренно игнорирующий SIGTERM; FFmpeg проверяется отдельной интеграцией. Mock не используется.

## Доказательства после исправления

В повторном диагностическом прогоне monitor PID 15 / PPID 11 / PGID 15 / SID 15 и FFmpeg PID 16 / PPID 15 / PGID 15 / SID 15 наследовали fd 8. После смерти worker monitor был усыновлён PID 1, FFmpeg остался его потомком. Оба сохраняли общий lock inode 1880051. Ожидающий worker PID 48 не имел lock в fdinfo и не расходовал попытку.

Объём сегментов снова вырос с 221464 до 2647040 байт. После SIGKILL группы не осталось живых monitor/FFmpeg с lock; внешний претендент получил блокировку того же inode, без удаления файла. Восстановление задания `3254c72c-96cf-4c05-b0f2-c98b564ab03b` завершилось историей ровно `RETRY / worker_lost`, `SUCCEEDED`; опубликован один `video.ready` с event ID `0c6908c9-bb56-46ef-a96b-6ed3ff8c85e2`, проверены manifest, HTTP 200 и равенство хэша оригинала до/после.

Инвариант: пока живой писатель имеет унаследованный дескриптор, конкурент не получает lock. Смерть одного monitor не освобождает lock живого писателя. После остановки всех держателей освобождается прежний inode. Поиск и убийство посторонних процессов или удаление lock-файлов в исправлении отсутствуют.

## Результаты проверок

| Проверка | Фактический результат | Доказательство в пакете Review |
|---|---|---|
| Исходный orphan на опубликованном S04 | Ожидаемый отказ воспроизведён: группа убита, BusyBox watchdog держит lock | `evidence/before.log`, `before/*.json`, worker logs |
| Исправленный orphan, диагностика | PASS: lock при записи; свободен после остановки группы; тот же inode; оригинал не изменён | `evidence/after.log`, `after/*.json` |
| Отдельные process/flock тесты | 3/3 PASS, финальный запуск 7.072 с | `evidence/process-tests-final.log` |
| Crash до коммита и после коммита до ack | PASS: manifest восстановлен, дубль подтверждён, событие и попытка прежние | `evidence/full-faults.log` |
| Потеря HLS-сегмента | PASS: закрыт HTTP, manifest отклонён, повтор worker_lost → SUCCEEDED | тот же полный журнал |
| Разрыв PostgreSQL при публикации | PASS: тот же worker восстановился, один успешный результат | тот же полный журнал |
| Orphan в полном исходном smoke | PASS: 1272572 → 2647040 байт при удержанном lock; новый worker ждёт; ровно RETRY/worker_lost и SUCCEEDED | тот же полный журнал |
| Потеря канала RabbitMQ | PASS: старый encode остановлен, RETRY/channel_lost → SUCCEEDED | тот же полный журнал |
| Таймаут настоящего FFmpeg 2 с | PASS: одна failed попытка ffmpeg_timeout, выдача закрыта, ready отсутствует | тот же полный журнал |
| Повреждённый оригинал и DLQ | PASS: одна failed попытка invalid_video, 404, реальное сообщение DLQ | тот же полный журнал |
| Broker ack timeout | PASS: redelivery через 122.8 с, одна успешная попытка, тот же event, очередь пуста | тот же полный журнал; исходное ожидание 180 с не менялось |
| Полный `smoke_s04_faults.py --ack-timeout` | PASS, exit code 0; сценарии после orphan выполнены | `evidence/full-faults.log` |
| Django на PostgreSQL | 15/15 PASS, 1.152 с | `evidence/django-tests.log` |
| Миграции на пустом томе и существующей БД | PASS; 0001–0003 применены с нуля, повторный migrate: No migrations to apply | `evidence/migrations-fresh.log`, `migrations-existing.log` |
| Отсутствие новых миграций | PASS: No changes detected | `evidence/makemigrations.log` |
| Контроль данных после интеграции | 9 отдельных видео, по одному job/generation=1; 7 SUCCEEDED, 2 ожидаемых FAILED; ready ровно один для успешного; manifest проверены, неповреждённые оригиналы совпали с fixture | `evidence/database-results.json` |
| Прежняя политика брокера и остановка | Восстановлена исходная политика 14400000; очереди без сообщений/consumers; только собственные стенды остановлены, тома сохранены | `policy-restored.json`, `queues-final.json`, `containers-final.json` |
| Whitespace, полный patch, секреты, границы scope | PASS: diff --check, также новые файлы; patch проверен обратным git apply --check; точные тестовые секреты и распространённые ключи/токены в пакете не обнаружены; другие worktrees сохранены | `evidence/git-checks.txt`, `secret-scan.txt`, `evidence/other-workspaces-verified.txt` |

Процессные тесты также прошли предварительный запуск (7.233 с); финальный повтор выполнен после добавления в тест ожидания появления временного output. Отрицательная проверка версии на исходном BusyBox-образе сохранена как `baseline-guard.log`. Вспомогательный сборщик итогов БД первоначально был запущен без PYTHONPATH и не импортировал приложение (`collector-missing-pythonpath.log`); повтор с указанным в инструкции PYTHONPATH успешно собрал JSON. Это не отказ приложения и не повтор полного smoke.

Рабочие журналы полного smoke сняты во время его выполнения в `evidence/full-worker-logs/`; это промежуточная копия, а не финальный лог ack worker. Итоговый полный stdout/stderr и exit code подтверждают последнюю проверку redelivery. Тестовые секреты, сами оригиналы и тома в ZIP не включены.

## Воспроизведение и внешняя конфигурация

Все ресурсы изолированы: baseline Compose project `vidharbor-s04-supervisor`, порт 8110; исправленный стенд `vidharbor-s04-supervisor-after`, порт 8111. У проектов разные `postgres_data`, `rabbitmq_data`, `media_data`; имя БД обоих — `vidharbor_s04_test`, как требует исходный smoke. Другие сервисы не запускались и не останавливались. Штатные worker/relay тестового проекта не запускаются: smoke сам создаёт worker-процессы и публикует команды.

В облаке использованы внешние файлы `/workspace/scratch/vidharbor-s04-supervisor/{prepare.py,env.sh,after.sh,compose.json,compose-after.json,api.Dockerfile,web.Dockerfile,diagnose.py}`. Никакой из них не является скрытой необходимой частью production. Без файлов среды проверки воспроизводятся следующим образом:

1. В чистом checkout нужной версии создать отдельную папку результатов, три случайных тестовых секрета (`django_secret_key`, `postgres_password`, `rabbitmq_password`); каталог секретов 0700, файлы 0444, без вывода значений. Задать отдельные `COMPOSE_PROJECT_NAME`, `VIDHARBOR_PORT`, `VIDHARBOR_DB_NAME=vidharbor_s04_test`, `VIDHARBOR_SECRETS_DIR` и `VIDHARBOR_PUBLIC_BASE_URL`. Не подключать существующие тома.
2. При прямом доступе к реестрам использовать штатные Dockerfile. Для облачного TLS-прокси использовать приложенный `prepare.py`: он генерирует Dockerfile с BuildKit secret `proxy_ca` из системного CA bundle для pip/npm/apk; в проверке сертификатов исключений нет. Также делает `/app` читаемым UID 65532 и заменяет wget healthcheck API/upload на Python urllib к loopback из-за настройки прокси в среде. Исходные Dockerfile, кроме установки coreutils, и health contracts не меняются. Абсолютные пути в helper необходимо адаптировать к выбранным checkout и папке результатов.
3. Создать `fixtures/landscape.mp4`: `ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc2=size=1280x720:rate=12 -f lavfi -i sine=frequency=440:sample_rate=44100 -t 10 -c:v libx264 -preset ultrafast -threads 2 -pix_fmt yuv420p -c:a aac fixtures/landscape.mp4`. Каталоги bind mounts должны быть проходимы UID 65532, fixture/скрипты — читаемы. Только папка тестовых доказательств допускает запись этого UID; каталог секретов не открывается.
4. Собрать образы, поднять только db, rabbitmq, migrate, api, upload, web. Смонтировать fixtures в `/fixtures:ro`, исходный `scripts/smoke_s04_faults.py`, приложенный `diagnose.py` и новый `scripts/smoke_s04_supervisor.py` в читаемую папку `/review`, а `/review/evidence` сделать доступной для записи. Если меняются имена Compose projects, использовать соответствующие новые image tags либо явные aliases, как в `compose-after.json`.
5. Выполнить в одноразовом upload-контейнере с `PYTHONPATH=/app:/review`: `python -u /review/smoke_s04_supervisor.py`. Он использует только собственные временные файлы и процессы. Для диагностического baseline выполнить `python -u /review/diagnose.py before` на исходном образе; для исправленного — `python -u /review/diagnose.py after` на новом отдельном стенде. До запуска папки marker/log должны быть пустыми. Контроллер не удаляет lock. Baseline оставляет зависшее задание в отдельной тестовой БД, поэтому его нельзя смешивать с исправленным стендом.
6. Для запросов из smoke задать **оба** `NO_PROXY` и `no_proxy` со значением `127.0.0.1,localhost,web,api,upload,db,rabbitmq`; задать `PUBLIC_BASE_URL=http://127.0.0.1:<порт>`. Для полного smoke сохранить JSON политики брокера и временно заменить только `consumer-timeout` на 60000 через `rabbitmqctl set_policy --apply-to quorum_queues vidharbor-jobs '^video[.]jobs$' '<JSON>'`. Остальные поля сохраняются: `dead-letter-exchange=video.dead`, `dead-letter-routing-key=video.malformed.v1`, `dead-letter-strategy=at-least-once`, `overflow=reject-publish`. Это политика только отдельного тестового брокера; production policy 14400000 не меняется.
7. В одноразовом upload запустить **неизменённый** `python -u /review/smoke_s04_faults.py --ack-timeout`. SHA-256 smoke в checkout и bind mount: `3a538fda1ac97b08a8567b5e63e3baf7b904e08123290372db812ab2b87897fe`. Assertions и ожидания исходного smoke не сокращались и не увеличивались. Сохранить stdout/stderr; после завершения вернуть исходную политику и проверить её JSON.
8. Проверить `python manage.py makemigrations --check --dry-run`, `python manage.py migrate --noinput`, `python manage.py test vidharbor --noinput -v 2`, `git diff --check`. В проверке с нуля миграции выполняются migrate-сервисом на отдельном пустом томе и Django runner на отдельной тестовой PostgreSQL БД. Проверка обновления — повторный migrate существующей S04 БД с данными; новой схемы исправление не требует.
9. Остановить только созданные тестовые проекты, без удаления томов. Проверить неизменность других worktrees. Не публиковать изменения без независимого Review.

## Границы и остаточные риски

Реальные PostgreSQL, RabbitMQ, FFmpeg, файловый том, HTTP выдача HLS и GNU timeout отделены от инъекций сбоев: harness добавляет замедление/loop к настоящему FFmpeg, останавливает worker, закрывает канал, завершает PostgreSQL backend и временно сокращает broker ack timeout. Это управляемые отказы, а не имитация успешного encode или брокера. Django HTTP-тесты используют test client; relay unit tests используют Mock канала. Отдельные process-тесты используют Python writer, не FFmpeg.

Не проверены в этом проходе: авария питания/хоста и долговечность диска, browser playback/переключение качества, длительное трёхчасовое кодирование, все форматы и большие файлы, Linux-дистрибутивы кроме закреплённого Alpine, внешний CI, production. Короткая независимая отсечка проверяет механизм deadline, но не доказывает трёхчасовой soak. Изменение версии Alpine/coreutils требует повторить process-тесты и полный fault smoke; зависимость закреплена и при исчезновении пакета сборка должна явно завершиться ошибкой.

Эта проверка устраняет блокер S04 в указанном образе. Она не является независимой приёмкой T501.2, не завершает S05 и не меняет статусы других этапов. Commit, push, PR, merge и deployment не выполнялись.
