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

## T702: проверено на учебном стенде

82 Django-теста PASS на первой сборке T702; после добавления проверки durable retry gauge отдельно 5/5 metrics tests PASS. Итоговый полный набор будет повторён после T703. Реальная проверка `smoke_s07.py --monitoring`: PASS, trace 87a0688daaa0942932f52de9b574024f. Пять authenticated scrape targets, API error/latency, FAILED/DLQ, тестовая webhook запись, firing-alert при остановке RabbitMQ, продолжение READY при остановке Tempo, отказ и восстановление datasource при остановке Prometheus. Исходный READY Job/Attempt/Outbox/original hash не изменились.

Grafana 12.4.15 действительно отрисована Chrome 156.0.8078.79: 16 успешных datasource queries, без query errors и внешних browser requests. Log rotation: 6000 созданных записей → 2155 сохранённых, 3 файла. Все 10 контейнеров приложения используют json-file 1m × 3. Promtool: config PASS, 4 alert rules PASS. Retention: Tempo 1h; Prometheus 6h/128MB. Prometheus/Tempo/Grafana только loopback, OTLP и компонентные metrics без host ports; web блокирует metrics/internal.

Managed Docker использует VFS и cgroup driver=none. Полный Grafana image упёрся в ENOSPC. Применён только облачный override: официальный полный Grafana 12.4.15 musl distribution с проверенным SHA-256 7e93f632ee9629f920231f52d98a86cba441a81dd0f3914d7a074b541e13ee06 смонтирован в собственный тестовый Alpine-контейнер. Это реальные Grafana server/assets, а не заглушка; стандартный pinned image в публикуемом Compose ещё требует GitHub-run. Старые тома не удалялись. Настроенные mem_limit/cpus в этой среде не являются измеренным kernel enforcement.

Первичные ошибки нового harness (форма входа Grafana и query string datasource URL), proxy/NO_PROXY и права config исправлены; прежние FAIL-логи сохранены отдельно, не выдаются за PASS.

T703 и итоговая документация ещё выполняются. GitHub CI этой ветки — Not Run: push/PR не разрешены. Реальных n8n/Sheets, публичного deployment и внешнего backup provider в работе нет.
