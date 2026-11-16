# Production Practice: факты по VidHarbor

Дата: 2026-11-15. Статус: **учебный облачный стенд, частичное выполнение Homework 005**. Публикация ветки и серверный deployment не выполнялись. [Общий протокол](protocols/s05-s08-completion.md) · [RTM](requirements-traceability.md).

| Требование | Фактическое доказательство | Остаток |
|---|---|---|
| P01 Compose | Отдельные проекты для retry, faults, source и clean restore; реальный PostgreSQL/RabbitMQ/FFmpeg/Nginx/frontend | Серверное демо после разрешения |
| P02 Environment | `.env.example`, отдельные project/ports и cloud overrides вне Git | Подготовить выбранный серверный env без подмены текущего |
| P03 Секреты | Случайные временные файлы вне репозитория, секреты не входят в evidence | Постоянный secret management/backup encryption согласовать |
| P04/P05 live/ready | S01: 200 liveness, readiness 200 → 503 → 200 при отказе каждой зависимости | Полные component checks S07 |
| P06 JSON logs | Существующие diagnostics сохранены | T701, исходный согласованный план S07 не получен |
| P07 correlation | ID заданий/событий видны в доказательствах | Сквозное request/job/event correlation по S07 не выполнено |
| P08 trace | Not Run | HTTP → outbox → RabbitMQ → worker, Tempo по S07 |
| P09 метрики | Замеры ресурсов и задержек одного прогона | Это не Prometheus: T702 остаётся открытым |
| P10 панель | Not Run | Grafana и демонстрация поиска реального сбоя по S07 |
| P11 backup | Реальный pg_dump + media + холодный broker, manifest/hash; пауза 79.193 с | Постоянный независимый носитель, retention, RPO/RTO |
| P12 clean restore | Новая PostgreSQL/отдельные тома, 98 файлов, оригинал, свежие удаления, прежние Job/message IDs и raw DLQ | Потеря хоста не моделировалась; реальная Sheets не сверена |
| P13 процедуры | Команды запуска, update 0003 → 0006 и отдельного restore выполнены и записаны в operations/backup-restore | Production cutover и возврат внешних систем не разрешены; окончательный runbook review |

## Покрытие итоговой приёмки

AC-001, AC-004–006, AC-008 имеют локальные доказательства прав/переименования/повтора/удаления и реальной очереди. AC-002: валидный синтетический MKV 3,529,596,888 байт прошёл UI → очередь → FFmpeg → player, SHA-256 совпал. Это 40 с повторённого синтетического материала 4K FFV1, не длительный естественный фильм. Жёсткие лимиты CPU/RAM в данной Docker-среде не применяются (`cgroup driver=none`), поэтому приёмка под production caps остаётся открытой. AC-003: версии и конкретные браузерные результаты см. в протоколе, без переноса исторического PASS на непроверенную сборку. AC-007 не выполнен: настоящие n8n/Google Sheets недоступны и не изменялись. AC-009 частичен: clean restore есть, S07 и независимая долговечность ещё нет.

## Граница Homework и roadmap

Готовность локального пути VidHarbor не означает завершения Homework №005. KazanCourts P01–P13/F15 (X001), теория/Review 01/CP4–CP5 (X002), Homework Defense/менторский review/Learning Pack (X003) остаются отдельными задачами. Технические evidence можно использовать при объяснении Level 5 Track A (Compose, migrations, environments, rollback) и Track C (health, backup/restore, RPO/RTO); они не выставляют темам roadmap Done/Reviewed и не подтверждают понимание за владельца. CP5 требует практики, объяснения и отдельной проверки, а не одного работающего скрипта.
