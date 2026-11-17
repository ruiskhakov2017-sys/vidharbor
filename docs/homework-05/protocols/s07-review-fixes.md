# S07 — исправления после Review f1f1a47

Дата:17 ноября 2026, учебная облачная среда. Ветка feature/s07-observability, HEAD остаётся `f1f1a47b4a61de8fd84e357ec6c39855d13ef6fc`. Изменения **локальные, не закоммичены и не опубликованы**. Старый патч и evidence f1f1a47 сохранены. Новых миграций, изменений n8n/Sheets/внешнего хранилища/deployment нет.

## Замечание 1: скрытая папка GitHub artifact

В [workflow](../../../.github/workflows/s07-observability.yml) все шесть `--report`, mkdir, evidence safety check и upload path переведены с `.s07-evidence/` на `s07-evidence/`. Сохранены pinned upload-artifact, `if-no-files-found: error`, credential scan и always-cleanup. Скрытые файлы не разрешаются к загрузке обходным флагом. Добавлены шаги реального Vue upload и collector-stall regression; старые Django/S01 workflows не менялись.

**PASS локальных path/config checks**, [проверка путей](evidence/s07-review-fixes/review-artifact-paths.json), [actionlint exit0](evidence/s07-review-fixes/review-actionlint.exit). **GitHub Actions и наличие скачиваемого artifact на GitHub: Not Run.** Для этого требуется отдельная публикация изменённого workflow и настоящий run; старые зелёные runs/локальный lint это не доказывают. В этом исправлении commit/push/PR не выполнялись.

## Замечание 2: передача байтов и READY в одном trace

[sendFile/api](../../../web/src/api.ts) возвращает допустимый traceparent из ответа XHR PUT; [Vue](../../../web/src/main.ts) сохраняет его вместе с pending upload и передаёт в POST complete/повтор проверки. Для обычного запуска без telemetry заголовок отсутствует и поведение API не меняется. На сервере [write_content](../../../api/vidharbor/uploads.py) получил span `upload.receive`: чтение request stream, запись и fsync. Никакие байты/тело/название не попадают в атрибуты. Нового поля БД или изменения immutable video.ready нет.

Trace `b856dba695f1af05f463fdc0b849409c`: **8 spans**, HTTP PUT→upload.receive и HTTP complete→job.create→outbox.publish→worker.process→ffmpeg.encode/video.publish. Проверены trace ID, parent span IDs, вложенность приёма байтов, сервисы и ненулевые длительности. Client wall PUT **106.727мс**, server PUT 97.581мс, upload.receive **61.690мс**. Подготовка CSRF/чтение fixture вынесены за клиентский таймер. Server span не называется чистым сетевым временем: он включает чтение/запись/fsync; client wall также содержит сеть/HTTP.

[Сквозной отчёт](evidence/s07-review-fixes/review-transfer.json), [Tempo JSON](evidence/s07-review-fixes/review-transfer.trace.json). Существующая проверка durable Outbox/retry сохранена; новый Django-тест проверяет SDK parents и persisted carrier от PUT. Настоящий браузер **сам**, без подстановки header тестом, передал PUT parent в complete и получил READY: trace `d0cf8c4ea9042faad4a1f4460d14cdc6`, receive 58.905мс. [Chrome evidence](evidence/s07-review-fixes/review-browser-upload.json), [trace](evidence/s07-review-fixes/review-browser-upload.trace.json). Контекст pending upload хранится в памяти страницы, а не переживает reload; после постановки задания carrier durable в прежнем Outbox.

## Замечание 3: медленный collector не занимает HTTP API

[API render](../../../api/vidharbor/metrics.py) больше не регистрирует DatabaseCollector: scrape обслуживает только процессные counters/histograms. Опциональный [Compose](../../../compose.observability.yaml) добавляет **metrics** на том же app image,256 MiB/0.5 CPU, без host ports и media/journal volumes. [Prometheus](../../../config/observability/prometheus.yml) имеет6 authenticated app targets; standard Compose этот сервис не запускает.

[Sampler](../../../api/vidharbor/metrics_sampler.py) запускает один дочерний [read-only collector](../../../api/vidharbor/management/commands/collect_metrics.py): wall deadline2 с, SIGKILL process group, максимум0.5 с на reap. При kernel-uninterruptible child новые процессы не создаются, collection_up=0, sampling останавливается до operator restart. Это отдельно проверено mock-кейсом; реальный kill/reap — отдельным subprocess-тестом. SQL statement250 мс, lock100 мс; сессия read-only, общий wall budget покрывает connect/DNS/Rabbit RPC. Дочерние diagnostics идут stderr, чтобы не повреждать Prometheus stdout.

HTTP exporter читает готовый snapshot≤128KiB; при failed/invalid/stale>15 с прошлые business gauges не выдаются как свежие. Exporter остаётся живым/отвечает быстро, readiness503/collection_up0 видны, [новый alert](../../../config/observability/alerts.yml) срабатывает при недоступности collection. Интервал5 с между сборами. Никаких неограниченных thread/child очередей, SQL/Rabbit waits в API и изменения бизнес-статусов ради metrics.

**Настоящий fault**: тот же sampler/server, но только у disposable collector в тестовом project inner SQL timeout снят и выполнен PostgreSQL `pg_sleep(30)`. Внешний deadline действительно оборвал процесс за **2.005 с**. Во время нескольких таких циклов **32** параллельных HTTP catalogue запросов и **64** authenticated scrape: все200, max catalogue **50.992мс**, max scrape **38.404мс**. Job/state/Attempt/Outbox snapshot до/после совпал. После возврата обычной команды collection_up вернулся1. Только SQL sessions с уникальным test application_name отменены в finally. [Отчёт](evidence/s07-review-fixes/review-final-collection.json), [лог](evidence/s07-review-fixes/review-final-collection.log), [скрипт](../../../scripts/smoke_s07_collection.py).

## Регрессия и пределы доказательств

| Проверка | Результат |
|---|---|
| Django/PostgreSQL telemetry ON |91 PASS /0 FAIL,22.217с; [лог](evidence/s07-review-fixes/review-final-django.log) |
| Тот же набор OFF |91 PASS /0 FAIL,22.352 с; [лог](evidence/s07-review-fixes/review-final-django-default.log). Не182 уникальных теста |
| Миграции |No changes detected, migrate --check exit0; [лог](evidence/s07-review-fixes/review-migrations.log) |
| Supervisor / restore guards |3/3 и5/5 PASS; [supervisor](evidence/s07-review-fixes/review-supervisor.log), [restore](evidence/s07-review-fixes/review-restore-guards.log) |
| S01 |Chrome/Vue; live200, ready200→503→200 при PG/Rabbit outage; [лог](evidence/s07-review-fixes/review-s01.log) |
| Полный S07 smoke |PASS:6 targets,8 spans, failed Job/DLQ, webhook test receiver, Tempo/Prometheus/Rabbit outage/recovery; [отчёт](evidence/s07-review-fixes/review-e2e.json). Таймер client PUT уточнён отдельным последним transfer run выше |
| Frontend |Настоящий новый `tsc --noEmit && vite build`, не cached build step; [build log](evidence/s07-review-fixes/review-build.log) |
| Promtool |Config PASS,5 rules PASS; [лог](evidence/s07-review-fixes/review-promtool.log) |
| Grafana / rotation |16 успешных datasource queries, видимый canvas; rotation6000→2155lines/3files PASS. [Dashboard](evidence/s07-review-fixes/review-dashboard.json), [скриншот](evidence/s07-review-fixes/review-dashboard.png), [rotation](evidence/s07-review-fixes/review-rotation.json) |
| GitHub CI/artifact availability |**Not Run**, новая ветка/изменения не публиковались |

Cloud использует VFS/cgroup driver none. При одновременном recreate возник ENOSPC; удалены только свои остановленные/created S07 контейнеры **без volumes** и dangling build cache; запуск повторён последовательно. Старые worktrees и данные сохранены. Managed overrides/официальный Grafana musl package прежние; новые tests загружены в собственный upload, а актуальный metrics_sampler.py read-only bind-mounted в upload/metrics для проверки окончательного source без нового VFS build. Это **не** финальная проверка official image в GitHub. Source modules, protected files и отсутствие публичных ports проверены: [source/scope](evidence/s07-review-fixes/review-source-scope.json). Четыре старых worktree побайтово сохранены, включая MERGE_HEAD/index/dirty/untracked S06: [проверка](evidence/s07-review-fixes/review-preservation.json). После проверок свой S07 stack остановлен без удаления томов; restart policy снята только с собственных worker/relay/cleanup, чтобы они не возобновились при новом старте среды. Heavy3–5GB/backup restore не повторялись.

Между f1f1a47 и текущими правками добавлены8 tests (transfer chain +7 sampler/SQL cases), поэтому83→91. Существующие S05/S06/S08 гарантии/тесты сохранены. Подключение реальных n8n/Sheets, внешний HTTPS/caps/backup carrier, независимый Review, исходный S07 plan и завершение Homework остаются открытыми. [Manifest](evidence/s07-review-fixes/manifest.json).

## Дополнение: первая публикация и диагностика GitHub CI

PR #11 открыт; локальные исправления выше опубликованы коммитом a647131. Django и S01 прошли, первый S07 run44090393332 завершился FAIL на отсутствии свежего collector snapshot. Видимая папка artifact уже работала: ZIP с acceptance.json скачан и проверен. Второй run44091359499 воспроизвёл отказ; diagnostics.json показал0OOM, cpu.max50000/100000,9deadline timeouts, CPU throttling и успешный direct collector6.3с с доступными PG/Rabbit. В занятом контейнере этот direct замер включает конкуренцию с background sampler.

Текущая конфигурация: metrics1CPU вместо0.5CPU,256MiB; collect_metrics.requires_system_checks=[] исключает повторный URL/system-check bootstrap в каждом дочернем процессе. Общие Django checks остаются в startup/отдельном91-test job. Deadline2с и все функциональные assertions не ослаблены. Окончательный GitHub результат и downloadable full evidence будут отражены в PR после фактического завершения, здесь PASS нового run заранее не заявлен. Merge/deployment не выполняются.
