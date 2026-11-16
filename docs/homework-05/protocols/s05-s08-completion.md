# S05–S08: объединённый локальный результат и остаток

Дата: 2026-11-15. Рабочая папка `/workspace/vidharbor-completion`, ветка `feature/s05-s08-completion`, база опубликованного main **047577f23b867daee68a9ca82dbbe1cb66f6dd49** (PR #8, принятый S04 supervisor fix). Ветка не отправлялась на GitHub; PR, merge и серверного deployment не было. **S05–S08 целиком не завершены:** ожидаются исходный план S07, реальная учебная интеграция и production решения.

Существующий T501.2 импортирован из `review/t5012-package` (исходный ZIP SHA-256 `8cee22ccfbfc3edf79fc5bc1eb6f415d096c306ef528692ef20da6ee0303aacc`, проверены 52 записи manifest). Нужные изменения S06 перенесены из существующего worktree; его merge не завершался/не отменялся. [Проверка сохранности](evidence/s05-s08/preservation-final.json) сравнила HEAD, branch, status, index, MERGE_HEAD, diff и untracked hashes четырёх прежних worktree: всё совпало. Исходный S08 Draft в отдельном worktree сохранён, SHA-256 `7903975f430cf12e4f3ffc89dd844a8564e7af6fb4a8efa536f41198ac8b6439`.

## Что реализовано по задачам

| Задача | Локальный результат | Доказательство / оставшаяся часть |
|---|---|---|
| T501.1 | Сохранён принятый rename, согласован с revoke/delete, повторена браузерная регрессия | [Rename](evidence/s05-s08/rename-regression.log); историческая интеграция READY/original/HLS: [S04/S05](s045-integration.md) |
| T501.2 | Принятый retry по failed_job_id, один successor; исправлен timeout ffprobe, оригинал/Upload/URL не меняются | [Реальный retry](evidence/s05-s08/retry-e2e.log), [72 Django](evidence/s05-s08/ci-local.log), [upgrade](evidence/s05-s08/upgrade.log) |
| T502 | Durable intent вне БД, отзыв поколения/URL, отмена Job/Delivery, остановка FFmpeg, cleanup под тем же flock | [Delete PROCESSING](evidence/s05-s08/deletion-e2e.json), Django права/CSRF/гонки |
| T503 | Pending/in-flight HTTP, поздний ACK, повтор delete, cleanup restart и stale DB restore проверены | [Delete](evidence/s05-s08/deletion-e2e.log), [restore functional](evidence/s05-s08/restore-functional.json); настоящий n8n/Sheets Not Run |
| T601 | HMAC/test receiver и защита URL реализованы | Host allowlist, все DNS/IP, pinned IP/TLS hostname, без redirects/ambient proxy. Секрет, учебные workflow/вкладка и внешняя HLS-ссылка ещё нужны |
| T602 | Подключён **существующий S04 Outbox video.ready**, отдельные ReadyEvent/Delivery сохраняют ID/данные, S06 резервирует одну строку | [Настоящий HTTP тест](evidence/s05-s08/notifications-e2e.log); реальная строка Sheets Not Run |
| T603 | Retry расписание 0/1/5/15/45 минут, конечное окно/lease fencing/manual retry — Django; lost reply/different-ID conflict — настоящий HTTP | Пять временных точек проверены тестовыми часами, часовой wall-clock прогон не заявлен. Внешний прогон открыт |
| T701 | Не начинался заново | Исходный согласованный план 2d65cb9 не получен; по указанию владельца реализация ожидает файл |
| T702 | Не реализован | Нет доказательств Prometheus/Tempo/Grafana, полной component readiness и ротации |
| T703 | Текущий CI сохранён/дополнен guards; job локально проверен | S01 Compose, полный Django/PostgreSQL, supervisor, migration drift, cleanup. Полный S07 CI и новый GitHub run открыты |
| T801 | Согласованный настоящий backup PG/media/cold RabbitMQ, manifest/hash, короткая пауза | [Backup](evidence/s05-s08/backup-final.log), [метаданные](evidence/s05-s08/backup-summary.json); независимый carrier/retention/RPO/RTO не утверждены |
| T802 | Чистая БД и отдельные тома; свежие удаления, оригинал, queue obligation, raw DLQ, allocator и закрытый gate проверены | [Restore](evidence/s05-s08/restore.log), [state](evidence/s05-s08/restore-state.json), [functional](evidence/s05-s08/restore-functional.json); реальная Sheets не сверена |
| T803 | Реальные start/update/restore; инструкции созданы, upgrade 0003 → 0006 не потерял данные | [Operations](../operations.md), [backup/restore](../backup-restore.md), [upgrade](evidence/s05-s08/upgrade.log); независимый runbook review и production cutover открыты |
| T804 | Большой прогон и текущая RTM сверка выполнены | [Large JSON](evidence/s05-s08/large-e2e.json); итоговая приёмка блокируется S07, внешней интеграцией и проверкой production caps |

## Проверки и воспроизведение

Docker Engine **29.9.2**, Compose **2.40.3**, настоящий PostgreSQL 16/RabbitMQ 4.2, FFmpeg 8.1.3, GNU coreutils timeout **9.11**. Точные закреплённые образы/пакеты — compose.yaml и Dockerfile. Для управляемого облака использованы **вне Git** build overrides: доверенный proxy CA через BuildKit secret, читаемость `/app` для UID 65532, healthcheck через loopback urllib без proxy. Бизнес-код и тесты те же; это ограничивает утверждение об идентичности окружения GitHub runner. Стандартная конфигурация требует отдельного GitHub Actions run после разрешения на push.

| Проверка | Фактический результат | Лог |
|---|---|---|
| Полный Django на новой PostgreSQL | **72 PASS / 0 FAIL**, 17.483 с в последнем CI-equivalent прогоне | [ci-local.log](evidence/s05-s08/ci-local.log) |
| Migration drift | PASS, `No changes detected` | Тот же лог |
| Upgrade 0003 → 0006 | PASS, сохранены Upload/Video/Job/Attempt/Outbox, retry_of nullable для старых Job, unique successor enforced | [upgrade.log](evidence/s05-s08/upgrade.log) |
| GNU supervisor/flock | **3 PASS / 0 FAIL**; смерть monitor/worker, живой writer и освобождение прежнего lock | [ci-local.log](evidence/s05-s08/ci-local.log) |
| Host restore guards | **5 PASS / 0 FAIL**; tar escape/link, совпадение source/target, live writer, auto-restart старого worker/migrate, чужой project | [final-guards.log](evidence/s05-s08/final-guards.log) |
| Smoke S01 | PASS, UI, live=200 и ready 200→503→200 для каждой зависимости | [s01-regression.log](evidence/s05-s08/s01-regression.log) |
| Smoke S02 | PASS, два автора/CSRF, abort/new upload, lost reply, один original/Job/Outbox, IDs/full persistent payload в настоящем RabbitMQ, nack/requeue | [s02-regression.log](evidence/s05-s08/s02-regression.log) |
| Полный S04 fault suite | PASS, включая surviving FFmpeg/flock, crash до/после commit, PostgreSQL/channel/ack timeout, partial 404 и DLQ | [s04-faults-regression.log](evidence/s05-s08/s04-faults-regression.log) |
| T501.2 реальный FFmpeg | PASS, поколения 1/2 FAILED timeout, поколение 3 READY; четыре параллельных запроса: 201 + 200 + 200 + 200 | [retry-e2e.log](evidence/s05-s08/retry-e2e.log) |
| Delete + cleanup restart | PASS, PROCESSING→CANCELLED, немедленный отказ новых URL, нет READY-event; cleanup restart→DELETED, файлов нет, один intent | [deletion-e2e.json](evidence/s05-s08/deletion-e2e.json) |
| Тестовый webhook | PASS, commit→lost reply→same event/row; разные IDs в пустой строке: written и 409/blocked | [notifications-e2e.log](evidence/s05-s08/notifications-e2e.log) |
| Frontend build | PASS, TypeScript и Vite, production bundle | [build-stack.log](evidence/s05-s08/build-stack.log) |
| Форматы и нативные браузеры | PASS, MP4/MOV/MKV/WebM → HLS, guest search, Auto/все уровни для landscape/portrait/small в Chrome 157.0.8103.78, Edge 157.0.4341.60, Firefox 159.0 | [native-matrix.log](evidence/s05-s08/native-matrix.log) |
| После restore | PASS, большой MKV в этих трёх браузерах, original/hash/delete/Job/DLQ/allocator | [restore-functional.json](evidence/s05-s08/restore-functional.json) |
| Повтор reconcile текущего кода | PASS, два одинаковых результата, нет лишних команд; snapshot/source/ledger проверены | [final-reconcile.log](evidence/s05-s08/final-reconcile.log) |
| Durable source fence | PASS, restart=no у 7 source containers, включая migrate; running/restarting отказали бы открытию | [durable-fence.log](evidence/s05-s08/durable-fence.log) |
| Workflow/static/links/secret scan | См. фактический финальный вывод проверок | [final-checks.log](evidence/s05-s08/final-checks.log) |
| GitHub Actions для этой ветки | **Not Run**: push/PR не разрешены. Зелёный исторический main не выдаётся за проверку новой ветки | Workflow синтаксис и локальный эквивалент проверены, временные CI volumes удалены |

Воспроизводимые основные команды (из корня с отдельными `COMPOSE_PROJECT_NAME`, портом, секретами и нужными cloud override):

```bash
docker compose --env-file .env.example config --quiet
docker compose --env-file .env.example build upload
docker compose --env-file .env.example up -d --wait db
docker compose --env-file .env.example run --rm --no-deps upload python manage.py makemigrations --check --dry-run
docker compose --env-file .env.example run --rm --no-deps upload python manage.py test vidharbor --noinput
python3 scripts/test_s08.py
python scripts/smoke_s01.py
python scripts/smoke_s02.py first.mp4 second.mp4
python scripts/smoke_s05_rename.py
python scripts/smoke_s04.py fixtures --state state.json
```

S02 требует **relay ON, worker OFF**, нулевое число consumers, одноразовый broker. Исправленный smoke проверяет фактическую доставку вместо случайного `sent_at IS NULL`. S04 fault suite запускается в отдельном project без обычного relay/worker: он сам управляет обработчиком; тестовая broker policy возвращена после прогона. См. CLI `--help` соответствующих scripts. Для supervisor использовался `upload python /review/smoke_s04_supervisor.py` с read-only bind скрипта и `PYTHONPATH=/app`; для upgrade — отдельная БД **vidharbor_s05_upgrade** и `scripts/check_t5012_upgrade.py`.

Для браузеров установлены пользовательские Chrome/Edge из официальных пакетов и Playwright Firefox. Firefox с нормальным sandbox успешно проверен вне оболочки, делающей `/proc/self/uid_map` read-only. Приложение и его конфигурация ради браузерного PASS не менялись. Незавершающийся `video.play()` в тесте получил явный 45-секундный отказ; критерии фактического движения времени/размеров видео сохранены.

## Большой файл и ресурсы

Валидный MKV: **3,529,596,888 байт**, SHA-256 **0c2159c564f894b4046bd9779831a18ced2327acdaf5569003ec6456da905307**. Это 20 повторений двухсекундного синтетического 3840×2160/10 fps FFV1 + PCM: 40 с настоящих media packets, без padding/sparse allocation. [Происхождение/ffprobe](evidence/s05-s08/large-fixture.json). Повторённый образец проверяет большой объём и обычный pipeline, не стоимость обработки многочасового естественного видео.

Обычный UI file input/XHR → оригинал → Outbox/RabbitMQ → штатный FFmpeg → READY → публичный player. Прогресс 0–100. Upload **25.968 с**, обработка после upload **54.439 с**, весь сценарий **88.636 с**. Сохранённый original имеет тот же размер и SHA; одно задание и один READY-event. В Chromium 156 исходный большой прогон прошёл Auto и все 360/480/720 варианты. После restore это же видео сыграло в Chrome/Edge/Firefox.

Пиковая сумма process RSS в PID namespace: API **101.7 MiB**, upload **137.5 MiB**, worker **398.2 MiB**; shared pages могут считаться повторно. Каталог: **52/52 HTTP 200**, максимум **0.114 с** во время обработки. Свободно на общем диске до 46,423,339,008 байт, минимум 40,907,915,264; параллельные builds/test volumes входят в этот расход, вся разница не приписывается ролику.

В этой среде Docker `cgroup driver=none`: объявленные cpus/mem_limit **не применялись**. CPU worker достигал 277.8% одного ядра. Первый Docker stats показал одинаковую общую память у разных контейнеров и был отклонён как измерение; принятый прогон использует `/proc` RSS/CPU ticks. Устойчивость под настоящими production caps и серверный performance budget ещё проверить.

## Backup, restore и границы доказательства

Согласованный комплект получен при остановленных writers. **Пауза 79.193 с**, source затем возобновлён. После snapshot через API удалён ролик A, который в копии ещё READY; свежий журнал экспортирован отдельно после повторного fence source. Restore: **новая PostgreSQL, отдельные media/RabbitMQ/journal volumes**, публичный gate 503 до проверки. `pg_restore --exit-on-error`, совпали **98 файлов** и записи Upload 5 / Video 5 / Job 5 / Attempt 4 / Outbox 8 / ReadyEvent 2 / Delivery 2 / ReceiverRow 2 / SheetAllocator 1. Затем применён более свежий deletion authority.

Restore занял **106.147 с**, отдельное открытие **23.710 с**. Это отдельные таймеры, а не полный end-to-end RTO с ручными паузами. После открытия удалённый A: DELETED, original отсутствует, старые watch/master/segment — 404. Большой B сохранил SHA и воспроизвёлся. Незавершённый C сохранил **job 0f28b15d-63cf-4728-8651-820361b4118c**, generation=1, одну успешную Attempt и один READY-event; команда не потеряна. RUNNING Attempt сохранение проверено Django и crash-регрессией S04; отдельный cold restore именно активного FFmpeg не заявляется.

Cold RabbitMQ сохранил raw DLQ `ee5c28e5-f717-4114-8e69-0f78040860f4` с прежним SHA тела. Durable rows 2/3 не переиспользованы: событие C получило **row=4**, allocator=5, один ReceiverRow после повторной доставки. Реальные Sheets/in-flight n8n не сверялись, notifier profile остаётся выключенным.

При возобновлении облачной машины source worker/relay автоматически стартовали из-за `restart: on-failure`; его БД оставалась выключенной. Добавлен отдельный `s08.py fence`: restart policies сохраняются в receipt и меняются на `no` только у writers своего source, останавливаются также restart loops. Restore/open теперь проверяют отсутствие живого writer **и** возможности автоперезапуска. От ошибочного ручного `compose up source` или внешнего администратора этот учебный барьер не защищает; запрещён такой запуск после cutover. Полная повторная имитация рестарта Docker после исправления не выполнялась, чтобы не затронуть чужие проекты; policies реально сверены, отрицательные guards проверены.

[Пошаговая процедура и отказные условия](../backup-restore.md). Независимый от хоста носитель, шифрование/retention и целевые RPO/RTO ещё согласовать. Same-host bundle и отдельный journal volume не являются резервом от потери хоста. Backup/dump/session/cookie/секреты не включены в review evidence.

## Первоначальные ошибки и исправления

| Ошибка | Причина и результат |
|---|---|
| S01 CLI Chromium timeout исторического прогона | Переход тестового запуска на Playwright/system Chromium с сохранением health/UI assertions; текущий S01 PASS |
| S02 sent_at | Relay успевал подтвердить сообщение раньше проверки NULL. Теперь строгие original/Job/Outbox/ID + реальное получение/full payload/persistence/confirm, без гонки на промежуточном значении |
| Restart upload → 504 | Nginx продолжал обращаться к прежнему Docker IP. Добавлен resolver Docker DNS/dynamic upstream; тест больше не делает ручной reload, реальный retry/restart PASS |
| Backup resume | Compose не поддерживал `start --wait`; заменено на `up --no-deps --wait`, принятый backup завершился и возобновил source |
| Tar ownership облачного bind | Файл, созданный Docker, виделся с unmapped UID; выход заранее создаётся владельцем executor с 0600, затем atomic rename. Принятый комплект/restore PASS |
| Firefox crash/play hang | Оболочка делала uid_map read-only; подтверждено на минимальной странице и логах. С нормальным Firefox sandbox вне этой оболочки реальные player checks PASS; приложение не менялось |
| Resume среды оживил старые writers | Добавлены durable fence/restart=no, остановка restarting services и отрицательные guards. Барьер операционный, только для отдельного учебного source |

## Итоговая сверка требований и открытые решения

| Задача → P | AC-001–009 / применимые F | Факт |
|---|---|---|
| T801 → P11 | AC-002/009; F01/02/13/16 | Настоящий backup/hash/pause PASS; carrier/retention/targets открыты |
| T802 → P12 | AC-001/003/004/005/006/008/009; F01/03/04/06/08/09/11/12/17 | Clean restore/оригинал/удаление/обязательство/DLQ/player PASS в указанных сценариях |
| T802 → P12, S06 | AC-007; F05/10 | Только тестовый receiver/ledger PASS, реальные n8n/Sheets **Not Run** |
| T803 → P13 | AC-008/009; F13/16/17/18 | Start/upgrade/restore и инструкции есть; независимый review/production cutover не выполнены |
| T804 → P11–P13 | AC-001–009; F01–F18 | Факты выше и в RTM; F14/S07 Not Run, F15 KazanCourts вне этой реализации. Это не декларация полного покрытия всех F |

AC-001/004/005/006/008 имеют локальные доказательства прав, публичного пути, управления, повтора и реальных сбоев. AC-002/003 проверены на конкретных больших/малых/портретных образцах и браузерах; production caps требуют отдельного прогона. **AC-007 не завершён. AC-009 частичен: S07 P06–P10 не выполнены.** FR-011/012, T601–603 и весь S06 не закрыты реальными Sheets. Отдельно [RTM](../requirements-traceability.md), [Production Practice](../production-practice.md), [tasks](../../../tasks.md).

| Решение | Учебный вариант / оставшееся согласование |
|---|---|
| A1 | Реальная общая остановка/pg_dump/media/cold broker; согласовать production максимум паузы и всех внешних writers |
| A2 | fsync/hash-chain authority в отдельном journal volume и свежий экспорт; согласовать независимость носителя, trusted watermark/retention/host-loss модель |
| A3 | Проверена холодная копия pinned broker + scoped DB obligations и сохранённая raw DLQ; production identity/cookie/version и ресурсный бюджет согласовать |
| A4 | Durable reservation/high-watermark, прежний snapshot/row, local receiver; нужны реальные event_id/строки/поздние запросы внешней Sheets |
| A5 | Closed RESTORE_MODE, отдельный notifier profile, durable source fence; production gate/администратор/порядок внешнего открытия на review |
| A6 | Измерен same-host drill; независимый carrier/расходы, retention, RPO/RTO и реальные CPU/RAM limits ещё утвердить |

Для серверного demo путь **upload → обработка → просмотр** работоспособен на восстановленном облачном loopback `http://127.0.0.1:8113/`; большой ролик `cdd32e7e-1fda-4ce1-8d04-71c6639912f6`. Это адрес внутри облачной машины, не общедоступный сервер. DNS/TLS, публичный URL, сервер и deployment не настраивались. Перед показом серверного demo нужны отдельное согласование выкладки, ресурсы и smoke на выбранном сервере.

Следующие зависимости: получить существующий план S07 (не перепланировать), выполнить его T701–703; согласовать учебный n8n/Sheets/public HLS и прогнать реальные внешние сценарии; утвердить carrier/retention/RPO/RTO/caps и production procedure. KazanCourts, теория Level 5 Track A/C, CP5/Defense и менторский review остаются отдельными задачами; эти evidence не присваивают roadmap темам Done/Reviewed.
