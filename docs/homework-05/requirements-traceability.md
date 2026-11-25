# VidHarbor — матрица требований и доказательств

**Статус** · Сверка фактов на 2026-11-17 после Review S07. Принятый S05/S06/учебный S08 и rollback deletion fix опубликованы в main 73db264. В отдельной ветке S07 локально проверены P06–P10; GitHub CI/Review новой ветки, настоящий n8n/Sheets, публичный deployment/caps и независимый backup carrier остаются открытыми. «На каркасе подтверждено» — историческая проверка минимального VidHarbor; «Частично» сохраняет непроверенную часть более широкого требования. Технический PASS не означает проверенное понимание пользователя.

[PRD](../product-spec.md) · [Архитектура](design.md) · [Решения](decision-log.md) · [Надёжность](reliability.md) · [План этапов](../implementation-plan.md) · [Задачи](../../tasks.md).

Локальные первоисточники: `Mentor_Homework_005.md` ред. 2 и `DZ5_New_Video_Project_Practical_Spec_RU.md`. DEC-003 и PRD фиксируют актуальные решения пользователя. P01–P13 и F13/F14/F16–F18 нужно подтверждать отдельно по двум приложениям. F15 и M28 относятся к KazanCourts. M13 входит в Core по контексту, Q и F — внутренние критерии проверки.

## Продукт и качество

Источник: PRD 0.3.

| ID | Требование | Решение / документ | Этап и задача | План проверки | Факт |
|---|---|---|---|---|---|
| FR-001 | Вход и кабинет | design §§5–6, PRD §6 | S02 / T201, S05 / T501.1 | AC-001 | Локально проверены два автора, вход, CSRF и запрет чужой загрузки/rename/retry/delete. [Сводный протокол](protocols/s05-s08-completion.md) |
| FR-002 | Загрузка и прогресс | design §§5–6, PRD §6 | S02 / T202 | AC-002 | Локально: обычный UI, прогресс 0–100, валидный MKV 3,529,596,888 байт. [Сводный протокол](protocols/s05-s08-completion.md) |
| FR-003 | Оригинал и задание | design §§5–6, PRD §6 | S02 / T203 | AC-002, AC-006 | Оригинал большого файла имеет тот же SHA-256 после upload и clean restore; одно задание/READY-event. [Сводный протокол](protocols/s05-s08-completion.md) |
| FR-004 | Лестница без upscale | design §§5–6, PRD §6 | S04 / T401, S04 / T404 | AC-003 | Проверены горизонтальная и портретная лестницы и малый источник без увеличения. [S04](testing-report.md#s04) |
| FR-005 | Каталог и поиск | design §§5–6, PRD §6 | S04 / T403 | AC-004 | Каталог и поиск проверены; после revoke видео скрыто, старые watch/HLS закрыты. [Сводный протокол](protocols/s05-s08-completion.md) |
| FR-006 | Страница и плеер | design §§5–6, PRD §6 | S04 / T403, S04 / T404 | AC-003, AC-004 | Прямая страница и воспроизведение проверены в Chrome, Edge и Firefox. [S04](testing-report.md#s04) |
| FR-007 | Один master | design §§5–6, PRD §6 | S04 / T403 | AC-003 | Один master на опубликованное видео, проверены плейлисты и сегменты. [S04](testing-report.md#s04) |
| FR-008 | Переименование и удаление | design §§5–6, PRD §6 | S05 / T501.1, S05 / T502 | AC-005 | Локально реализованы rename/delete, права и CSRF; проверены стабильный URL при rename и отзыв новых запросов при delete, cleanup после restart. Независимая итоговая приёмка впереди. [Сводный протокол](protocols/s05-s08-completion.md) |
| FR-009 | Состояния в кабинете | design §§5–6, PRD §6 | S02 / T203, S03 / T302, S04 / T403 | AC-002, AC-006 | Кабинет показывает ход обработки и готовую ссылку. При окончательной ошибке API вернул FAILED без ссылки. [S04](testing-report.md#s04) |
| FR-010 | Ручной повтор оригинала | design §§5–6, PRD §6 | S05 / T501.2 | AC-006 | Готовый T501.2 интегрирован: реальный FFmpeg timeout → READY, повтор из прежнего оригинала, lost response/restart/конкуренция без лишнего поколения. Полный S04 faults теперь PASS на базе с исправлением supervisor. [Сводный протокол](protocols/s05-s08-completion.md) |
| FR-011 | Webhook по готовности | design §§5–6, PRD §6 | S06 / T602 | AC-007 | Частично: реальный Outbox video.ready S04 подключён к Delivery, прежний ID/снимок, локальный HTTP получатель проверен. Реальные n8n не проверялись; требование целиком открыто. [Сводный протокол](protocols/s05-s08-completion.md) |
| FR-012 | Строка Sheets | design §§5–6, PRD §6 | S06 / T602 | AC-007 | Частично: durable reservation, одна тестовая строка, lost reply/retry/conflict проверены. Реальная Google Sheets Not Run; требование целиком открыто. [Сводный протокол](protocols/s05-s08-completion.md) |
| NFR-001 | Гигабайты, 5 ГиБ и отсутствие лимита длительности | design §§6–8, reliability | S02 / T202, S08 / T804 | AC-002 | Локальный большой прогон 3.53 ГБ PASS; предел 5 ГиБ и отказ сверх тестового лимита проверены отдельно. 40-секундный синтетический ролик не доказывает любой длительный вход; hard CPU/RAM caps в этом облаке не действуют. [Сводный протокол](protocols/s05-s08-completion.md) |
| NFR-002 | Раздельные HTTP и worker | design §§6–8, reliability | S02 / T202, S03 / T301 | AC-002, AC-008 | Раздельные upload/API/worker: при большом прогоне каталог 52/52 HTTP 200, max 0.114 с, RSS API 102 MiB/upload 137 MiB/worker 398 MiB (сумма RSS процессов). [Сводный протокол](protocols/s05-s08-completion.md) |
| NFR-003 | Ограниченные попытки и DLQ | design §§6–8, reliability | S03 / T302, S03 / T303, S03 / T304 | AC-008 | Подтверждены три попытки, задержки и два пути доставки в DLQ на стенде. [S03](testing-report.md#s03) |
| NFR-004 | Отдельная доставка уведомлений | design §§6–8, reliability | S06 / T602, S06 / T603 | AC-007 | Локально: отдельный notifier, пять запланированных попыток/lease fencing/отмена при delete, готовое видео независимо от HTTP. Внешняя приёмка открыта. [Сводный протокол](protocols/s05-s08-completion.md) |
| NFR-005 | Compose, конфиг и checks | design §§6–8, reliability | S01 / T102, S01 / T104, S07 / T702 | AC-009 | Частично: стандартный Compose/health сохранён, облачный S01 повторён с PG/Rabbit outage. Приватные component checks добавлены. GitHub новой ветки/публичные параметры Not Run. [S07](protocols/s07-observability.md) |
| NFR-006 | Наблюдение | design §§6–8, reliability | S07 / T701, S07 / T702 | AC-009 | Локально исправлены transfer-to-READY trace и изоляция медленного collector;8 spans/91 Django tests/real SQL fault. GitHub artifacts/Review/публичная приёмка открыты. [Исправления](protocols/s07-review-fixes.md) |
| NFR-007 | Копия и восстановление | design §§6–8, reliability | S08 / T801, S08 / T802, S08 / T803 | AC-009 | Частично: настоящий dump/media/cold broker и restore в чистую БД/тома, свежий журнал удалений. Независимый носитель/retention/RPO/RTO targets не согласованы. [Сводный протокол](protocols/s05-s08-completion.md) |
| AC-001 | Два аккаунта и запрет чужих действий | план §§6–7, PRD §8 | S02 / T201, S05 / T501.1 | F01 | Локальные Django/браузерные доказательства двух авторов, CSRF и запрета чужих rename/retry/delete. [Сводный протокол](protocols/s05-s08-completion.md) |
| AC-002 | Большой файл и целостность | план §§6–7, PRD §8 | S02 / T202, S02 / T203, S08 / T804 | F01, F02, F03 | Большой валидный синтетический MKV 3,529,596,888 байт прошёл UI/queue/FFmpeg/player; SHA совпал, замеры сохранены. Проверка под реально действующими production limits ещё нужна. [Сводный протокол](protocols/s05-s08-completion.md) |
| AC-003 | Воспроизведение доступных вариантов | план §§6–7, PRD §8 | S04 / T401, S04 / T404 | F04 | Подтверждено на согласованных образцах. Вертикальная рамка 720×1280, все доступные варианты воспроизводятся. [S04](testing-report.md#s04) |
| AC-004 | Публичный каталог и прямая ссылка | план §§6–7, PRD §8 | S04 / T403 | F04 | Публичный путь и каталог проверены; неготовые/удалённые поколения недоступны. [Сводный протокол](protocols/s05-s08-completion.md) |
| AC-005 | Переименование и отзыв ссылки | план §§6–7, PRD §8 | S05 / T501.1, S05 / T502 | F04 | Локально проверены rename/старая ссылка/HLS, затем delete: watch/master/сегменты закрыты, очистка после restart. Удаление после snapshot пережило restore. [Сводный протокол](protocols/s05-s08-completion.md) |
| AC-006 | Обрыв, ошибки, повтор из оригинала | план §§6–7, PRD §8 | S02 / T203, S03 / T302, S05 / T501.2 | F06, F08, F12 | Локально: abort/lost reply, два реальных FFmpeg timeout, успешный retry без новой передачи, прежний original/URL и единственный successor; probe_timeout не расходует generation. [Сводный протокол](protocols/s05-s08-completion.md) |
| AC-007 | Реальная строка и отказ интеграции | план §§6–7, PRD §8 | S06 / T602, S06 / T603 | F05, F10 | НЕ ЗАВЕРШЁН: тестовый получатель PASS, настоящий VidHarbor → n8n → Google Sheets Not Run; нужны учебные workflow/вкладка, доступ и внешняя HLS-ссылка. [Сводный протокол](protocols/s05-s08-completion.md) |
| AC-008 | Реальная DLQ, сбои, дубли | план §§6–7, PRD §8 | S03 / T301, S03 / T302, S03 / T303, S03 / T304 | F03, F06, F07, F08, F09, F11, F12 | Полная отказная S04 регрессия PASS: orphan/flock, падения, БД/канал, ack timeout/redelivery, тот же event, partial 404/DLQ. [Сводный протокол](protocols/s05-s08-completion.md) |
| AC-009 | Compose, telemetry и чистый restore | план §§6–7, PRD §8 | S01 / T104, S07 / T701, S07 / T702, S08 / T802, S08 / T803 | F13, F14, F16, F17, F18 | Частично: Compose/health/telemetry и clean restore имеют учебные доказательства. Новый GitHub CI, независимый carrier и публичная приёмка Not Run. [S07](protocols/s07-observability.md) · [S08](protocols/s05-s08-completion.md) |

## Поручения ментора

Источник: ДЗ 005 §3.

| ID | Требование | Решение / документ | Этап и задача | План проверки | Факт |
|---|---|---|---|---|---|
| M01 | Загрузка в новом веб-приложении | design §§2–8, DEC-003 | S02 / T202 | F01 | Загрузка через Vue проверена в Chrome двумя авторами. [S02](testing-report.md#s02) |
| M02 | Сохранение оригинала | design §§2–8, DEC-003 | S02 / T203 | F01, F04 | Неизменённый оригинал подтверждён SHA-256 для двух файлов. [S02](testing-report.md#s02) |
| M03 | Отзывчивость | design §§2–8, DEC-003 | S02 / T202, S03 / T301 | F01, F02 | Частично. Кабинет отвечает во время медленной передачи на отдельном пуле. [S02](testing-report.md#s02) |
| M04 | Несколько гигабайт | design §§2–8, DEC-003 | S08 / T804 | F02 | Не выполнено |
| M05 | Варианты в фоне | design §§2–8, DEC-003 | S04 / T401, S04 / T402 | F03, F04 | Штатный worker подготовил HLS через RabbitMQ. [S04](testing-report.md#s04) |
| M06 | Потолок 720p и нижние качества | design §§2–8, DEC-003 | S04 / T401 | F04, U02 | Подтверждены горизонтальный предел 1280×720 и утверждённая портретная рамка 720×1280. [S04](testing-report.md#s04) |
| M07 | Очередь обработки | design §§2–8, DEC-003 | S03 / T301 | F03 | Outbox доставляет задания в quorum-очередь с исходным ID после восстановления RabbitMQ. [S03](testing-report.md#s03) |
| M08 | Один плейлист | design §§2–8, DEC-003 | S04 / T403 | F04 | Рабочий master и сегменты проверены HTTP-запросами. [S04](testing-report.md#s04) |
| M09 | Воспроизведение | design §§2–8, DEC-003 | S04 / T404 | F04 | Плеер проверен в трёх настольных браузерах. [S04](testing-report.md#s04) |
| M10 | Уведомление по готовности | design §§2–8, DEC-003 | S06 / T602 | F05 | Не выполнено |
| M11 | Настраиваемый webhook со ссылкой | design §§2–8, DEC-003 | S06 / T601, S06 / T602 | F05, F10 | Не выполнено |
| M12 | RabbitMQ | design §§2–8, DEC-003 | S03 / T301 | F03 | Проверены реальная очередь RabbitMQ и сохранение сообщений при перезапуске. [S03](testing-report.md#s03) |
| M13 | Реальная DLQ, Core по контексту | design §§2–8, DEC-003 | S03 / T303 | F06, F07 | Подтверждены отправка из outbox и dead-lettering RabbitMQ после восстановления DLQ. [S03](testing-report.md#s03) |
| M14 | Level 5 Track A | Отдельный учебный объём, план §1 | X002 | A01–A12 | Не выполнено |
| M15 | Level 5 Track B | Отдельный учебный объём, план §1 | X002 | B01–B06 | Не выполнено |
| M16 | Level 5 Track C | Отдельный учебный объём, план §1 | X002 | C01–C12 | Не выполнено |
| M17 | Полный Track D отложен | Отдельный учебный объём, план §1 | X002 | отдельный-учёт | Не выполнено |
| M18 | Production Practice для двух приложений | design §§2–8, DEC-003 | S08 / T804, X001 | P01–P13 | Не выполнено |
| M19 | Compose | design §§2–8, DEC-003 | S01 / T102, X001 | P01 | Частично. Compose запускает каркас VidHarbor с пустыми томами. [S01](testing-report.md#s01) |
| M20 | Environment | design §§2–8, DEC-003 | S01 / T104, X001 | P02 | Частично. Для каркаса VidHarbor проверен `.env.example`. [S01](testing-report.md#s01) |
| M21 | Секреты вне Git | design §§2–8, DEC-003 | S01 / T104, X001 | P03 | Частично. В каркасе VidHarbor локальные секреты исключены из Git. [S01](testing-report.md#s01) |
| M22 | Health/readiness | design §§2–8, DEC-003 | S01 / T104, S07 / T702, X001 | P04, P05 | Частично. В каркасе VidHarbor проверены отдельные `live` и `ready` при отказе зависимостей. [S01](testing-report.md#s01) |
| M23 | Структурированные логи | design §§2–8, DEC-003 | S07 / T701, X001 | P06 | VidHarbor: PASS JSON/allowlist/privacy на реальном стенде. KazanCourts отдельно. [S07](protocols/s07-observability.md) |
| M24 | Request/job ID | design §§2–8, DEC-003 | S07 / T701, X001 | P07 | VidHarbor: PASS correlation Upload→Job/Outbox/Rabbit/worker/READY, сохранение carrier при retry. [S07](protocols/s07-observability.md) |
| M25 | Сквозной trace | design §§2–8, DEC-003 | S07 / T701, X001 | P08 | VidHarbor: PASS настоящий распределённый trace, parent IDs, длительности, resource services и поиск логов. [S07](protocols/s07-observability.md) |
| M26 | Базовые метрики | design §§2–8, DEC-003 | S07 / T702, X001 | P09 | VidHarbor: PASS реальные success/error/DLQ/retry/webhook/outage/recovery метрики; production caps/SLO не утверждены. [S07](protocols/s07-observability.md) |
| M27 | Панель | design §§2–8, DEC-003 | S07 / T702, X001 | P10 | VidHarbor: PASS Grafana live datasource и Chrome; официальный image GitHub-run ещё Not Run. [S07](protocols/s07-observability.md) |
| M28 | Ограничение изменений KazanCourts | Отдельный учебный объём, план §1 | X001 | F15 | Не выполнено |
| M29 | Объяснение решений | Отдельный учебный объём, план §1 | X003 | Defense | Не выполнено |
| M30 | Сверка с ТЗ | design §§2–8, DEC-003 | D001, S08 / T804 | RTM | Сверка обновлена по фактам: AC-007/009, внешний n8n/Sheets, CI/Review S07, carrier/caps/public и учебная приёмка открыты. [S07](protocols/s07-observability.md) |
| M31 | Повтор непонятных тем | Отдельный учебный объём, план §1 | X002 | theory-notes | Материал по30темам подготовлен; усвоение/защита не проверены. [Theory notes](theory-notes.md) |
| M32 | Связь с ментором по готовности | Отдельный учебный объём, план §1 | X003 | mentor-review | Не выполнено |
| M33 | PostgreSQL backup и проверенный restore | design §§2–8, DEC-003 | S08 / T801, S08 / T802, X001 | P11, P12 | VidHarbor: реальный backup/clean restore PASS учебного стенда; carrier/retention/RPO/RTO и KazanCourts открыты. [Сводный протокол](protocols/s05-s08-completion.md) |
| M34 | Запуск, обновление и восстановление | design §§2–8, DEC-003 | S08 / T803, X001 | P13 | VidHarbor: локальные start/update/restore проверены и документированы; production/KazanCourts не завершены. [Сводный протокол](protocols/s05-s08-completion.md) |

## Надёжность

Источник: ДЗ 005 §4.3, практическое ТЗ §2.

| ID | Требование | Решение / документ | Этап и задача | План проверки | Факт |
|---|---|---|---|---|---|
| Q01 | Конечные повторы с паузами | reliability | S03 / T302 | F06 | Три попытки с задержками 30 и 120 секунд. [S03](testing-report.md#s03) |
| Q02 | Реальная DLQ | reliability | S03 / T303 | F06 | Окончательная ошибка и неразбираемое сообщение получены из DLQ. [S03](testing-report.md#s03) |
| Q03 | Изоляция плохого задания | reliability | S03 / T303 | F07 | После отправки неисправного задания в DLQ исправное завершилось. [S03](testing-report.md#s03) |
| Q04 | Восстановление worker | reliability | S01 / T103, S03 / T304 | F08 | Проверены восстановление после остановки worker, потери heartbeat и таймаута ack. [S03](testing-report.md#s03) |
| Q05 | Безопасный дубль | reliability | S03 / T304, S04 / T402 | F09 | Повтор доставки сохранил manifest, одну успешную попытку и прежний event_id. [S04](testing-report.md#s04) |
| Q06 | Готовность до webhook | reliability | S04 / T402, S06 / T602 | F05 | Локально: S04 атомарно сохраняет READY + Outbox; S06 использует этот же snapshot и отдельно доставляет после захвата под Video lock. [Сводный протокол](protocols/s05-s08-completion.md) |
| Q07 | Отдельный сбой webhook | reliability | S06 / T603 | F10 | PASS на тестовом получателе: отказ/потеря ответа не меняют Job или HLS; внешняя проверка Not Run. [Сводный протокол](protocols/s05-s08-completion.md) |
| Q08 | Разрыв между БД и брокером | reliability | S03 / T301, S03 / T304 | F11 | Проверены обрыв подтверждения публикации и обрыв связи worker с базой. [S03](testing-report.md#s03) |
| Q09 | Разбор и контролируемый повтор | reliability | S03 / T303, S05 / T501 | F06, демо-оператора | Локально проверены DLQ и контролируемый retry по failed_job_id без сброса attempts/оригинала. [Сводный протокол](protocols/s05-s08-completion.md) |
| Q10 | Лимиты и частичные результаты | reliability | S02 / T202, S04 / T402, S08 / T804 | F02, F12 | Большой файл, закрытые partial, FFmpeg deadline и исправленный ffprobe timeout проверены; hard resource enforcement на сервере не доказан. [Сводный протокол](protocols/s05-s08-completion.md) |

## Production Practice видеосервиса

Источник: ДЗ 005 §5, практическое ТЗ §3.

| ID | Требование | Решение / документ | Этап и задача | План проверки | Факт |
|---|---|---|---|---|---|
| P01 | Запуск целиком через Compose | design §§7–8 | S01 / T102, S08 / T803 | F13 | На каркасе подтверждено. Compose поднял все четыре компонента с пустыми томами. [T102](protocols/s01-runtime.md#t102) |
| P02 | Environment | design §§7–8 | S01 / T104 | F13 | На каркасе подтверждено. Настройки запуска заданы в `.env.example`. [T104](protocols/s01-runtime.md#t104) |
| P03 | Секреты вне Git | design §§7–8 | S01 / T104 | F13 | На каркасе подтверждено. Локальные файлы секретов исключены из Git, значения отсутствуют в файлах коммита. [T104](protocols/s01-runtime.md#t104) |
| P04 | Liveness | design §§7–8 | S01 / T104 | F13 | На каркасе подтверждено. `live` вернул `200` при остановке базы или очереди. [T104](protocols/s01-runtime.md#t104) |
| P05 | Readiness | design §§7–8 | S01 / T104, S07 / T702 | F13 | На каркасе подтверждено. `ready` вернул `503` при остановке каждой зависимости и `200` после восстановления. [T104](protocols/s01-runtime.md#t104) |
| P06 | JSON-логи | design §§7–8 | S07 / T701 | F14 | PASS учебного VidHarbor: 1997 JSON records проверены, технический allowlist без title/user/password/cookie/CSRF. [S07](protocols/s07-observability.md) |
| P07 | Request/job correlation ID | design §§7–8 | S07 / T701 | F14 | PASS учебного VidHarbor: correlation=Upload UUID, durable carrier/повторы; 7 linked events по trace ID. [S07](protocols/s07-observability.md) |
| P08 | Сквозной trace | design §§7–8 | S07 / T701 | F14 | PASS локально после Review: PUT/upload.receive→complete→Job/Outbox/Rabbit→FFmpeg→READY,8 spans; настоящий Vue передал parent. [Исправления](protocols/s07-review-fixes.md) |
| P09 | Базовые метрики | design §§7–8 | S07 / T702 | F14 | PASS локально после Review:6 private targets, durable gauges собираются отдельно от API с5 с полным subprocess deadline/SQL250 мс. Настоящий pg_sleep(30),32catalogue/64scrape и recovery проверены. [Исправления](protocols/s07-review-fixes.md) |
| P10 | Живая панель | design §§7–8 | S07 / T702 | F14 | PASS учебного VidHarbor: provisioned Grafana→Prometheus query, Chrome 16 datasource queries, fault/recovery и local alert firing. [S07](protocols/s07-observability.md) |
| P11 | PostgreSQL backup | design §§7–8 | S08 / T801 | F16 | Реальный pg_dump/media/cold broker, полный manifest/checksums, пауза 79.193 с. Same-host учебный drill; постоянный носитель не согласован. [Сводный протокол](protocols/s05-s08-completion.md) |
| P12 | Restore в чистой БД | design §§7–8 | S08 / T802 | F17 | Чистая PostgreSQL и отдельные тома: 98 файлов/записи совпали, fresh deletion authority применён; restore 106.147 с. [Сводный протокол](protocols/s05-s08-completion.md) |
| P13 | Запуск, обновление, восстановление системы | design §§7–8 | S08 / T803 | F18 | Локальные start/update 0003→0006/restore выполнены, инструкции созданы. Production cutover/runbook review и внешний доступ остаются открытыми. [Operations](operations.md) · [Сводный протокол](protocols/s05-s08-completion.md) |

## Сценарии демонстрации

Источник: ДЗ 005 §12, практическое ТЗ §4.

| ID | Требование | Решение / документ | Этап и задача | План проверки | Факт |
|---|---|---|---|---|---|
| F01 | Загрузка и неизменённый оригинал | план §§6–7 | S02 / T202, S02 / T203 | AC-002 | Большой оригинал прошёл обычный upload, SHA совпал до и после restore. [Сводный протокол](protocols/s05-s08-completion.md) |
| F02 | Большой файл с замерами | план §§6–7 | S08 / T804 | AC-002 | PASS учебного прогона 3.53 ГБ, времена/RSS/диск/каталог сохранены; production limits pending. [Сводный протокол](protocols/s05-s08-completion.md) |
| F03 | Реальный RabbitMQ и worker | план §§6–7 | S03 / T301 | AC-008 | Задание от загрузки прошло через RabbitMQ и штатный FFmpeg worker. [S04](testing-report.md#s04) |
| F04 | Лестница и плеер | план §§6–7 | S04 / T404 | AC-003 | Проверены лестница, Auto и ручной выбор качеств в трёх браузерах. [S04](testing-report.md#s04) |
| F05 | Webhook после готовности | план §§6–7 | S06 / T602 | AC-007 | Частично: настоящий READY → тестовый HTTP. Реальный n8n/Sheets Not Run. [Сводный протокол](protocols/s05-s08-completion.md) |
| F06 | Неисправная задача в DLQ | план §§6–7 | S03 / T303 | AC-008 | Подтверждена доставка неисправного задания в DLQ. [S03](testing-report.md#s03) |
| F07 | Исправная после плохой | план §§6–7 | S03 / T303 | AC-008 | Исправное задание завершилось после неисправного. [S03](testing-report.md#s03) |
| F08 | Crash/restart worker | план §§6–7 | S03 / T304 | AC-008 | На HLS проверена смерть worker при живом FFmpeg и восстановление. [S04](testing-report.md#s04) |
| F09 | Повторная доставка | план §§6–7 | S03 / T304, S04 / T402 | AC-008 | Повтор не изменил готовый manifest или событие. [S04](testing-report.md#s04) |
| F10 | Webhook недоступен | план §§6–7 | S06 / T603 | AC-007 | Частично: реальный тестовый HTTP commit → потеря ответа → повтор/конфликт; внешняя интеграция Not Run. [Сводный протокол](protocols/s05-s08-completion.md) |
| F11 | Брокер недоступен | план §§6–7 | S03 / T301, S03 / T304 | AC-008 | При недоступном RabbitMQ команда сохранилась в outbox. После запуска доставлена с исходным ID. [S03](testing-report.md#s03) |
| F12 | Частичный результат закрыт | план §§6–7 | S04 / T402 | AC-006, AC-008 | Неполный и неактивный результат закрыт. Полный результат принят после сбоя. [S04](testing-report.md#s04) |
| F13 | Compose, настройки и checks | план §§6–7 | S01 / T102, S01 / T104, S07 / T702, X001 | P01–P05 | Облачный S01 повторён на полном учебном стеке: browser/live/ready и обе зависимости; компонентные live/ready S07 проверены. Public/KazanCourts отдельно. [S07](protocols/s07-observability.md) |
| F14 | Telemetry | план §§6–7 | S07 / T701, S07 / T702, X001 | P06–P10 | PASS локальной telemetry VidHarbor P06–P10 с реальным trace/панелью/rotation/faults. GitHub нового SHA и публичный demo Not Run; KazanCourts отдельно. [S07](protocols/s07-observability.md) |
| F15 | Регрессия только KazanCourts | Отдельный учебный объём, план §1 | X001 | вне-scope | Не выполнено |
| F16 | Реальный dump | план §§6–7 | S08 / T801, X001 | P11 | PASS учебного dump + media/cold broker/manifest, независимый carrier pending. [Сводный протокол](protocols/s05-s08-completion.md) |
| F17 | Реальный restore | план §§6–7 | S08 / T802, X001 | P12 | PASS clean restore, оригиналы и свежие удаления проверены; внешняя Sheets не сверена. [Сводный протокол](protocols/s05-s08-completion.md) |
| F18 | Три эксплуатационные процедуры | план §§6–7 | S08 / T803, X001 | P13 | Локальные три процедуры записаны с фактическими командами. Deployment/production rollback не выполнялись. [Operations](operations.md) · [Сводный протокол](protocols/s05-s08-completion.md) |

## Исходные уточнения ментора

Источник: ДЗ 005 §8.

| ID | Требование | Решение / документ | Этап и задача | План проверки | Факт |
|---|---|---|---|---|---|
| U01 | Письменный список Production Practice получен | DEC-003/004, design §11 | D001 | P01–P13 | Источник получен, практика не выполнена |
| U02 | Качества, вертикаль и малый источник | DEC-003/004, design §11 | S04 / T401 | OPEN-02 | Рамка 720×1280 утверждена в DEC-005 и проверена на повёрнутом MOV. [S04](testing-report.md#s04) |
| U03 | Плейлист и плеер | DEC-003/004, design §11 | S04 / T403, S04 / T404 | OPEN-02 | Master и hls.js проверены в Chrome, Edge и Firefox. [S04](testing-report.md#s04) |
| U04 | Контракт webhook | DEC-003/004, design §11 | S06 / T601, S06 / T603 | OPEN-01 | Контракт существующего sender и синтетические сценарии точно записаны; реальная сериализация/доступ n8n/Sheets не согласованы и не проверены. [Подготовка](integration-rehearsal.md) |
| U05 | Пределы и загрузка | DEC-003/004, design §11 | S01 / T101, S02 / T202, S08 / T804 | OPEN-03 | Большой синтетический прогон выполнен, hash/ресурсы сохранены; точные условия и ограничения в протоколе. [Сводный протокол](protocols/s05-s08-completion.md) |
| U06 | Кандидат FFmpeg | DEC-003/004, design §11 | S04 / T401 | F04 | Закреплённая сборка FFmpeg/ffprobe 8.1.3 проверена на четырёх контейнерах. [S04](testing-report.md#s04) |
| U07 | Неясные слова о прежних интеграциях | Отдельный учебный объём, план §1 | X002 | theory-notes | Не выполнено |
| U08 | Граница внешнего deployment | DEC-003/004, design §11 | D001, S08 / T803 | P13 | Учебные эксплуатационные процедуры выполнены; внешнего deployment не было. [Сводный протокол](protocols/s05-s08-completion.md) |
| U09 | Срок и встреча | Отдельный учебный объём, план §1 | X003 | mentor-review | Не выполнено |

## Открытые вопросы Product Spec

| ID | Решение и остаток | Задача / проверка | Факт |
|---|---|---|---|
| OPEN-01 | Адрес и пять попыток согласованы. Нужны доступ, защита и испытание Sheets. | S06 / T601–T603, AC-007 | Не выполнено |
| OPEN-02 | HLS, hls.js, портретная рамка и набор образцов S04. | S04 / T401–T404, AC-003 | Рамка вертикали зафиксирована, образцы и браузерные проверки записаны. [S04](testing-report.md#s04) |
| OPEN-03 | 5 ГиБ, контейнеры и новый старт после обрыва приняты. Нужны допуск среды и замеры. | S01 / T101, S02 / T202, S08 / T804, AC-002 | Большой прогон 3.53 ГБ выполнен; production ресурсные пределы/допуск отдельного сервера остаются открытыми. [Сводный протокол](protocols/s05-s08-completion.md) |
| OPEN-04 | Политика удаления принята. Журнал удалений, место и срок копий уточняются. | S05 / T502–T503, S08 / T801–T802 | Учебные delete journal и restore проверены; независимость carrier и retention ещё согласовать. [Сводный протокол](protocols/s05-s08-completion.md) |

## Теория и общий учебный цикл

Источник — ДЗ 005 §6 и Markdown roadmap. Все строки относятся к X002 и [подготовленному материалу по30темам](theory-notes.md); понимание/Defense не проверены. Проверка — объяснение своими словами и применимый учебный пример. Технические артефакты VidHarbor дают материал для защиты, а статусы понимания фиксируются отдельно.

| ID | Тема | Задача и доказательство | Факт |
|---|---|---|---|
| A01 | Docker image / container | X002, объяснение и пример | Не выполнено |
| A02 | Volumes | X002, объяснение и пример | Не выполнено |
| A03 | Networking | X002, объяснение и пример | Не выполнено |
| A04 | Docker Compose | X002, объяснение и пример | Не выполнено |
| A05 | VPS vs PaaS vs Cloud | X002, объяснение и пример | Не выполнено |
| A06 | Reverse proxy | X002, объяснение и пример | Не выполнено |
| A07 | DNS | X002, объяснение и пример | Не выполнено |
| A08 | TLS / HTTPS | X002, объяснение и пример | Не выполнено |
| A09 | Environments | X002, объяснение и пример | Не выполнено |
| A10 | Secrets | X002, объяснение и пример | Не выполнено |
| A11 | Database migrations during deployment | X002, объяснение и пример | Не выполнено |
| A12 | Rollback | X002, объяснение и пример | Не выполнено |
| B01 | Build | X002, объяснение и пример | Не выполнено |
| B02 | Lint / static checks | X002, объяснение и пример | Не выполнено |
| B03 | Tests | X002, объяснение и пример | Не выполнено |
| B04 | Artifacts | X002, объяснение и пример | Не выполнено |
| B05 | Deployment | X002, объяснение и пример | Не выполнено |
| B06 | Rollback strategy | X002, объяснение и пример | Не выполнено |
| C01 | Structured logs | X002, объяснение и пример | Не выполнено |
| C02 | Metrics | X002, объяснение и пример | Не выполнено |
| C03 | Traces | X002, объяснение и пример | Не выполнено |
| C04 | Correlation IDs | X002, объяснение и пример | Не выполнено |
| C05 | Dashboards | X002, объяснение и пример | Не выполнено |
| C06 | Alerts | X002, объяснение и пример | Не выполнено |
| C07 | Health / readiness | X002, объяснение и пример | Не выполнено |
| C08 | Backups | X002, объяснение и пример | Не выполнено |
| C09 | Restore | X002, объяснение и пример | Не выполнено |
| C10 | Graceful shutdown | X002, объяснение и пример | Не выполнено |
| C11 | RPO / RTO — базово | X002, объяснение и пример | Не выполнено |
| C12 | SLI / SLO — базово | X002, объяснение и пример | Не выполнено |

CP4 и CP5 сверяются отдельно по фактическим доказательствам. Review 01, Defense 005 и Learning Pack относятся к общему учебному циклу. Актуальный Review 01 не приложен. Расхождение порога Defense между общим регламентом и ДЗ 005 §14.3 требуется сверить до X003.

## Дополнение от 20 ноября 2026 года · S06

Записи `Not Run` выше описывают более ранние прогоны. Формальный статус требований Homework №5 сохраняется. [Протокол реального прогона](protocols/s06-real-integration-2026-11-20.md).

| Требование | Подтверждено 20 ноября | Что осталось |
|---|---|---|
| F05 · Webhook после READY | READY реального ролика доставлен в тестовую копию n8n с реальной Google Sheets. Строка 3 вкладки `Videos` подтверждена `200 written` и чтением таблицы. Рабочий workflow вернул `written`, в PostgreSQL зафиксирован `WRITTEN`. | Отдельный execution ID рабочего workflow не найден. Первый новый ролик после запуска постоянного notifier не наблюдали. |
| F10 · Сбой и повтор уведомления | Ранний запрос вернул `503` без записи. Успешная запись получена отдельной проверкой. Повтор READY для строки 3 не создал дубль. Рабочий workflow вернул `403` на неверную подпись и `409` на конфликт строки. | Длительный отказ реальных Google Sheets с последующим автоматическим восстановлением не проверялся. |
| AC-007 · Реальная строка и отказ интеграции | Внешняя доставка, строка, повтор и защитные ответы подтверждены в указанных сценариях. | AC-007 остаётся открытым в части автоматической доставки нового ролика и восстановления после длительного отказа Sheets. |
