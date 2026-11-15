# T501.2 · Ручной повтор обработки — локальная реализация для Review

13 ноября 2026 года. Worktree /workspace/vidharbor-s05-completion, ветка feature/s05-completion. База — 1d609a710a7ba3c68d12b6d830d21b2344e3a7ca (main после PR #6). Нового commit, push, PR, merge и deployment нет. T502/T503 и интеграция S06 не реализовывались. Все результаты ниже относятся к этому проходу; весь S05 не завершён.

## Контракт и защита от дублей

POST /api/videos/{video_id}/jobs/{failed_job_id}/retry требует сессию владельца и CSRF. Новый запрос создаёт 201 с job (id, generation, state, retry_of) и текущим представлением video. Повтор принятого запроса возвращает 200 и именно ранее созданный job, включая его новое состояние. Это чтение сохранённого результата, а не новая обработка: разрешено вернуть его и при активном/готовом преемнике. Новый запуск для активного, готового или недоступного/удаляемого видео отклоняется 409. Чужой или несоответствующий видео job — 404; без сессии с корректным CSRF — 401, без CSRF — 403.

Миграция 0004 добавляет nullable OneToOne Job.retry_of → Job с PROTECT и related_name manual_retry. Уникальность на исходном job защищает от второго преемника, прежняя уникальность video/generation сохранена. Старые записи имеют NULL; история попыток и DLQ не переписывается.

Сначала проверяется уже существующая связь повтора. Для нового запуска берётся стабильный per-video flock S04 без удержания строк БД. Под ним проверяются завершённая Upload, обычный файл без symlink, длина, SHA-256 и реальный ffprobe. Длительное чтение происходит вне транзакции. Затем короткая транзакция блокирует Video и исходный Job, повторно проверяет состояние/поколение и атомарно сохраняет generation, новый Job.retry_of и outbox video.transcode.v1 с attempt_no=1. Публикацию выполняет прежний relay с confirms/mandatory. При ошибке outbox откатываются все три изменения.

storage.media_lock получил необязательный timeout; значение по умолчанию 0 сохраняет прежнее неблокирующее поведение. Ручной повтор ждёт до 5 секунд. Занятый lock даёт 423 с Retry-After: 1, без задания и смены поколения. Исчезнувший/повреждённый/неподдерживаемый оригинал даёт 422 с предложением загрузить исправный файл; недоступность проверки/чтения — 503 с Retry-After. Получение уже сохранённого результата не требует повторного чтения оригинала.

Разрешённые причины окончательного сбоя: worker_lost, channel_lost, media_io, media_unavailable, disk_full, ffmpeg_timeout. Последняя Attempt должна быть FAILED. invalid_video и неоднозначный ffmpeg_decode_or_encode требуют исправного исходника; прочие неизвестные/invalid_hls причины требуют диагностики, а не безусловного повтора. Это консервативная политика, явное расширение которой требует Review.

Запрос направляется Nginx в существующий upload-пул с ffprobe, proxy_next_upstream off и read timeout 120 секунд. API-пул продолжает обслуживать каталог. Кабинет получает job_id/can_retry/retry_advice для FAILED и показывает кнопку повтора. При неопределённом ответе сохраняет исходный failed_job_id для «Повторить запрос», даже если опрос изменил текущее задание. После успешного чтения результата новая явная попытка адресует новое failed job. Выход очищает pending; отмена/переименование и защита от устаревшего poll сохранены.

## Стенд и команды

Отдельный проект Compose vidharbor-t5012, порт 8107, база vidharbor_s04_test; отдельные postgres/rabbitmq/media volumes. Upgrade проверяется в дополнительной БД vidharbor_s05_upgrade того же тестового PostgreSQL. Исходные рабочие пространства S07/S06/feature/s05-rename не изменялись. Рабочие сервисы других проектов не останавливались.

Python 3.13.18, PostgreSQL 16.14, FFmpeg 8.1.3; образы и зависимости закреплены в репозитории. Браузер — установленный Chromium 156.0.8078.79. Реальные n8n/Sheets не использовались. Локальные helpers и журналы — /workspace/scratch/vidharbor-t5012; безопасные helpers входят в пакет Review, секреты не входят.

prepare.py создаёт внешние Dockerfile с CA secret-mount для pip/npm/apk и читабельными для UID 65532 копиями /app. compose.json задаёт build secrets и локальный healthcheck через Python urllib: BusyBox wget в этой облачной среде отправляет loopback через proxy. env.sh задаёт только имена проекта/БД, порт, адрес и путь секретов. TLS и проверки целостности не отключались. Подготовка не меняет Dockerfile и Compose репозитория. Helpers — конфигурация воспроизведения теста, а не настройка production.

Основные команды из worktree, Bash; сначала выполнить prepare.py из пакета Review и загрузить env.sh, проверив пути и свободный порт. Для нового стенда создать новые секреты/тома; для повтора на тех же томах сохранить прежние секреты. Фикстуры генерируются локально FFmpeg; команды включены в пакет Review.

```sh
source /workspace/scratch/vidharbor-t5012/env.sh
COMPOSE_PARALLEL_LIMIT=2 docker compose --env-file .env.example -f compose.yaml -f /workspace/scratch/vidharbor-t5012/compose.json build
docker compose --env-file .env.example -f compose.yaml -f /workspace/scratch/vidharbor-t5012/compose.json up -d --wait --wait-timeout 240 db rabbitmq migrate api upload web
docker compose --env-file .env.example run --rm --no-deps upload python manage.py test vidharbor --noinput -v 2
docker compose --env-file .env.example run --rm --no-deps upload python manage.py makemigrations --check --dry-run
python3 scripts/smoke_t5012.py /workspace/scratch/vidharbor-t5012/fixtures/landscape.mp4
python3 /workspace/scratch/vidharbor-t5012/regress_s04.py
python3 /workspace/scratch/vidharbor-t5012/run_chromium.py scripts/smoke_s045_ready_rename.py /workspace/scratch/vidharbor-t5012/fixtures/landscape.mp4
python3 scripts/smoke_s05_rename.py
python3 /workspace/scratch/vidharbor-t5012/regress_s01.py
```

smoke_t5012 управляет только этим тестовым worker/relay/брокером. Сначала останавливает worker/relay, затем загружает файл; это исключает обработку до установки тестового FFMPEG_TIMEOUT=1. После restart upload в rootless Docker менялся IP; existing static upstream Nginx обновляется nginx -s reload. Такое восстановление стенда указано явно и не выдаётся за автоматическое обнаружение нового IP приложением.

## Фактические результаты

| Проверка | Результат и доказательство |
|---|---|
| Django на реальном PostgreSQL, новая тестовая БД с миграциями 0001–0004 | PASS: 33 теста (исходный прогон — 8,195 с), включая 18 новых ReprocessingTests; django-final.log |
| Четыре конкурентных Django-запроса | PASS: одна 201, три 200, один job, одна outbox и generation=2; реальные DB locks/flock |
| Уникальность связи retry_of в БД | PASS: второе создание преемника отвергнуто IntegrityError |
| Rollback между созданием job и outbox | PASS: исключение внедрено patch; generation/job/связь/outbox откатились; следующий запрос успешен |
| Старый запрос после FAILED преемника и после следующего нового повтора | PASS: тот же job; новое поколение создаёт только явный запрос по новому failed_job_id |
| Владелец, CSRF, отсутствие сессии, активный/готовый/чужой/stale job | PASS; DELETING/DELETED выставлялись в тестовых данных, реального удаления ещё нет |
| Missing/hash mismatch/symlink/повреждённый контейнер/реальный AVI вне whitelist | PASS: отказ без нового job; ffprobe настоящий, для AVI/нечитаемого контейнера хэш совпадал с тестовой записью |
| Недоступный ffprobe, занятый flock, повтор результата после исчезновения оригинала | PASS; недоступность ffprobe внедрена patch, flock и файл настоящие |
| Реальный FFmpeg timeout → ручной повтор → ещё один timeout → успешный следующий повтор | PASS: поколения 1/2 FAILED, поколение 3 SUCCEEDED; smoke-t5012-final.log |
| Потеря ответа API, 4 HTTP replay, restart upload и RabbitMQ outage | PASS: одно поколение 2, тот же job и message_id, outbox сохранился до восстановления публикации |
| Четыре конкурентных реальных HTTP-запроса создания | PASS: одна 201 и три 200, один job поколения 3 |
| Оригинал и ссылка | PASS: SHA-256, inode, mtime и вся Upload совпали до/после; watch/master URL прежние; нового upload нет |
| HLS после повтора | PASS: 39 сегментов HTTP, g3, Auto и все три качества воспроизведены |
| Реальная RabbitMQ DLQ | PASS: оба terminal event поколений 1/2 найдены в очереди с теми же message_id/payload, outbox помечены отправленными; сообщения сохранены через nack/requeue; real-dlq.log |
| Миграция 0003 → 0004 существующих данных | PASS: Upload/Video/Job/Attempt/Outbox сохранены, старые retry_of NULL, второй преемник запрещён; migration-upgrade.log |
| makemigrations --check --dry-run | PASS: No changes detected; migration-check.log |
| TypeScript/Vite и Docker build | PASS; build-final.log, build-web-final.log, build-tests-final.log |
| Регрессия S04 MP4/MOV/MKV/WebM | PASS: размеры, H264/AAC, HTTP master/варианты/сегменты, закрытые пути, поиск, Chromium Auto/manual; regression-s04.log |
| T501.1 READY rename и stale poll | PASS: исходный тест с адаптацией только браузерного executable, без изменения assertions; regression-ready-rename.log |
| T501.1 форма/отмена/503/HTML/права/CSRF | PASS: исходный smoke_s05_rename.py; regression-rename.log |
| S01 health при отказе БД/брокера и экран входа | PASS: прежние check_health/assertions и Playwright вместо локального CLI; regression-s01.log |
| Исходный smoke_s01.py с Chromium --dump-dom | FAIL среды: прямой CLI завершился ошибкой, затем timeout 30 с с отдельным profile. Не объявляется успешным; оба журнала сохранены |
| S02 при остановленном RabbitMQ | PASS: два автора, прогресс, обрыв, новый upload, потеря ответа/повтор complete, ID/SHA/job/outbox, CSRF и выход; regression-s02.log |
| S04 fault: rename до commit, дубль после commit, повреждённый manifest, потеря PG connection | PASS в выполненной части исходного smoke_s04_faults.py; общий запуск не завершился успешно |
| S04 fault: orphan FFmpeg/flock | FAIL: новый worker не получил lock после killpg; подтверждено отдельной проверкой неизменённой базы; regression-s04-faults.log, baseline-orphan.log |

Контрольный видео-ID: 7fbaf627-6598-4569-af9a-4557ba58cfce. Job поколения 1: 27501365-28f2-4488-b86b-f45b5e711665; поколения 2: 29ee36d1-395b-44b1-8579-88fa081cf5a2; поколения 3: d826fdfc-657d-4709-853c-715648f1defd. SHA-256 оригинала до/после: b7078db80cbc692637c6f6ce0ac637293b1af8dda8b0a7de6796dd4d77ad1b57.

После предварительного Review помощник тестов переименован из fail() в mark_job_failed(), чтобы не перекрывать unittest.TestCase.fail(). test_receipt_replay_when_successor_active_or_ready выставляет SUCCEEDED/READY напрямую в PostgreSQL и проверяет только получение сохранённого результата. Он не подтверждает FFmpeg, публикацию или доступность HLS: их доказательство — отдельный smoke_t5012.py с настоящим worker и HTTP.

Основной сквозной тест вызывает реальный таймаут FFmpeg и останавливает настоящий RabbitMQ; потеря HTTP-ответа внедряется Playwright после получения ответа сервера. Проверка rollback использует исключение patch, а не падение PostgreSQL. Upgrade использует существующие записи, созданные historical models на схеме 0003, а не копию production-БД. Это разные уровни доказательств, не взаимозаменяемые имитации.

## Сбой регрессии S04 и ограничения

В smoke_s04_faults.py остановка worker оставляет наследуемый lock у дочерних процессов. В этой среде после killpg исходной группы отдельный процесс timeout продолжил держать descriptor. Новый worker остался ждать, и checkpoint RETRY_WAIT не наступил за бюджет теста. Повторён целевой orphan probe с /app из неизменённых файлов HEAD 1d609a7, без кода T501.2: убита группа 13, lock всё ещё удерживался процессом timeout PID/PGID 15. Базовая проблема воспроизведена; transcode/job_consumer/publication/job_attempts и relay в этом проходе не изменялись.

Автоматического исправления S04 не выполнялось. Общая отказная регрессия S04 не считается PASS. Следующие за orphan сценарии исходного запуска (channel loss, отдельный timeout/invalid-source/DLQ и ack timeout) этим запуском не выполнены. Реальные timeout и доставка DLQ дополнительно проверяются в основном сценарии T501.2 и операторской сверке, но это не подменяет пропущенные проверки исходного скрипта. Для завершения полного набора нужна отдельная оценка/исправление базового supervisor S04 и повтор регрессии.

Ещё Not Run: нативные Chrome/Edge/Firefox в этом проходе, отдельный файл 3–5 ГБ, host/power failure, полный набор таймеров S03, действующий n8n/Sheets, T502/T503. Матрица браузеров принятого S04 не переименовывается в повторно пройденную на основании Chromium. Хэширование большого оригинала и 120-секундный HTTP budget требуют отдельного измерения; при потере ответа URL с failed_job_id остаётся устойчивым ключом повтора.

Подготовительные ошибки стенда сохранены отдельно: ожидание upload до готовности процесса, смена IP и необходимость Nginx reload, тестовый worker с прежним timeout, MOV без rotation metadata (исправлена генерация display_rotation=90), CLI Chromium. Они не маскируются как успешные прогоны и не потребовали изменения бизнес-логики S04. Результаты PASS в таблице относятся к исправленным процедурам и конечным журналам.

## Поставка и оставшаяся работа

Пакет независимого Review содержит полный changes.patch (включая новые файлы), итоговые файлы, полный текст файлов, безопасные helpers/команды и журналы с manifest SHA-256. Секреты, node_modules, медиа, дампы и прежний ZIP не включаются. Ничего не опубликовано в GitHub. T501.2 представлен для Review; наличие локальных PASS не закрывает S05 и не отменяет сбой базовой регрессии.

Остались: Review T501.2, отдельное решение по supervisor S04, T502/T503 и согласование переименования с удалением, внешняя часть после T602 и восстановление в T802. К этим задачам агент самостоятельно не переходит.
