# S07: автономная наблюдаемость — текущий протокол

База: main 73db264923a03edaf7c00d46a3951784d59b67e8, отдельный worktree `/workspace/vidharbor-s07-observability`, ветка feature/s07-observability. Старый worktree S06 с merge не используется и не изменяется.

Оригинальный implementation-plan-s07.md/2d65cb9 недоступен: локального объекта/ветки/worktree/файла нет, remote refs S07 отсутствуют, GitHub lookup коммита вернул 422. Здесь реализуется только явно разрешённый владельцем scope P06–P10 из задания от 16 ноября 2026, не выдуманный согласованный план. Получение и последующая сверка оригинала остаются открытыми.

## T701: проверено

- Полный PostgreSQL/Django набор: 78 PASS, 0 FAIL (включая 4 новых теста диагностики и SDK context).
- Настоящий небольшой HTTP upload → PostgreSQL Outbox → RabbitMQ → FFmpeg worker → READY: PASS.
- Tempo trace `1875329ce781ff38a7c632aa4efebeac`: HTTP complete 88.55 ms, job.create 9.25 ms, outbox.publish 6.68 ms, worker.process 1875.75 ms, ffmpeg.encode 1802.25 ms, video.publish 5.30 ms. Проверены trace IDs, родительские span IDs, ненулевые длительности и resource.service.name upload/relay/worker.
- Correlation ID = UUID Upload; Outbox сохраняет W3C carrier, relay передаёт дочерний traceparent в RabbitMQ headers, worker извлекает его в отдельном потоке. Автоматический retry копирует сохранённый carrier. Ручной retry имеет новый HTTP trace и прежний correlation Upload; бизнес-ID/поколения и snapshot video.ready не меняются.
- Проверены 103 JSON записи и 7 связанных по trace ID событий. Test title/username/password/cookies/CSRF отсутствуют в журнале. Formatter не сериализует произвольные message/args/exception text; spans не содержат тел/headers/query strings.
- OTLP BatchSpanProcessor: очередь 256, batch 32, период 500 ms, HTTP timeout 1 s. Внешние OTLP endpoints запрещены текущей конфигурацией; бизнес-обработка не ждёт экспортера.

Артефакты до финального копирования evidence: `/workspace/.vidharbor-cloud/s07/t701-tests.log`, `t701-e2e.json`, `.trace.json`, `.logs.jsonl`. До успешного прогона исправлены права на bind-mounted Tempo config в managed cloud и конфликт имени http в новом тестовом harness. Эти первичные ошибки не считаются PASS.

T702/T703 и итоговая документация ещё выполняются. GitHub CI этой ветки — Not Run: push/PR не разрешены. Реальных n8n/Sheets, публичного deployment и внешнего backup provider в работе нет.
