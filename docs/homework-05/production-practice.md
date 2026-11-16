# Production Practice: факты по VidHarbor

Дата: 2026-11-16. Статус: **учебный облачный стенд, частичное выполнение Homework 005**. Принятый S05/S06/S08 опубликован в main 73db264; новая ветка S07 и серверный deployment не публиковались. [Общий протокол](protocols/s05-s08-completion.md) · [RTM](requirements-traceability.md).

| Требование | Фактическое доказательство | Остаток |
|---|---|---|
| P01 Compose | Отдельные проекты для retry, faults, source и clean restore; реальный PostgreSQL/RabbitMQ/FFmpeg/Nginx/frontend | Серверное демо после разрешения |
| P02 Environment | `.env.example`, отдельные project/ports и cloud overrides вне Git | Подготовить выбранный серверный env без подмены текущего |
| P03 Секреты | Случайные временные файлы вне репозитория, секреты не входят в evidence | Постоянный secret management/backup encryption согласовать |
| P04/P05 live/ready | S01 повторён; API200→503→200 при PG/Rabbit outage; relay/worker live 200/ready 503 при broker down | Публичная эксплуатация/жёсткие caps отдельно |
| P06 JSON logs | 1997 JSON records, технический allowlist/privacy PASS | Review/CI новой ветки и KazanCourts отдельно |
| P07 correlation | HTTP/Upload/Job/Outbox/Rabbit/worker/READY связаны; carrier через retry, 7 linked events | Сверка оригинального S07 plan после получения |
| P08 trace | Настоящие OTel SDK→Tempo spans с parent IDs/service/duration, trace 82f77f97135deec8813a64d7c8bab838 | GitHub официальный profile ещё Not Run |
| P09 метрики | 5 real app scrape targets; API/errors/duration/jobs/retry/DLQ/outbox/webhook; success/failure/recovery PASS | Production budget/SLO и webhook внешне не проверены |
| P10 панель | Реальный Grafana datasource→Prometheus, Chrome 16queries без ошибок/внешних browser requests; alert firing; rotation | Managed cloud использует проверенный official musl package в override; pinned Grafana image требует GitHub-run |
| P11 backup | Реальный pg_dump + media + холодный broker, manifest/hash; пауза 79.193 с | Постоянный независимый носитель, retention, RPO/RTO |
| P12 clean restore | Новая PostgreSQL/отдельные тома, 98 файлов, оригинал, свежие удаления, прежние Job/message IDs и raw DLQ | Потеря хоста не моделировалась; реальная Sheets не сверена |
| P13 процедуры | Команды запуска, update 0003 → 0006 и отдельного restore выполнены и записаны в operations/backup-restore | Production cutover и возврат внешних систем не разрешены; окончательный runbook review |

## Покрытие итоговой приёмки

AC-001, AC-004–006, AC-008 имеют локальные доказательства прав/переименования/повтора/удаления и реальной очереди. AC-002: валидный синтетический MKV 3,529,596,888 байт прошёл UI → очередь → FFmpeg → player, SHA-256 совпал. Это 40 с повторённого синтетического материала 4K FFV1, не длительный естественный фильм. Жёсткие лимиты CPU/RAM в данной Docker-среде не применяются (`cgroup driver=none`), поэтому приёмка под production caps остаётся открытой. AC-003: версии и конкретные браузерные результаты см. в протоколе, без переноса исторического PASS на непроверенную сборку. AC-007 не выполнен: настоящие n8n/Google Sheets недоступны и не изменялись. AC-009 частичен: clean restore и локальная telemetry есть; GitHub CI/Review S07, независимая долговечность и публичная приёмка открыты.

## Граница Homework и roadmap

Готовность локального пути VidHarbor не означает завершения Homework №005. KazanCourts P01–P13/F15 (X001), теория/Review 01/CP4–CP5 (X002), Homework Defense/менторский review/Learning Pack (X003) остаются отдельными задачами. Технические evidence можно использовать при объяснении Level 5 Track A (Compose, migrations, environments, rollback) и Track C (health, backup/restore, RPO/RTO); они не выставляют темам roadmap Done/Reviewed и не подтверждают понимание за владельца. CP5 требует практики, объяснения и отдельной проверки, а не одного работающего скрипта.

## Результат автономной подготовки

[S07: точный протокол и evidence](protocols/s07-observability.md). Новый workflow подготовлен, статически проверен и его основные сценарии выполнены в cloud; сам GitHub-run **Not Run**, публикация запрещена. Существующие workflows сохранены. JSON/trace/exporter outage не меняли Job/Attempt/READY/original, Prometheus restart восстановил dashboard, broker recovery завершил durable command с тем же trace.

Offline готовы: [контракт и матрица учебного n8n/Sheets](integration-rehearsal.md), [HTTPS/topology/resources/update/rollback](public-deployment.md), [варианты независимых копий/authority](backup-restore.md#независимый-носитель-подготовленные-варианты-не-внедрение), [30 тем для объяснения](theory-notes.md). Это подготовка, а не внешние PASS. Ни provider/бюджет/retention/RPO/RTO, ни production SLO не выбраны за владельца. P06–P10 VidHarbor не закрывают соответствующую практику KazanCourts.
