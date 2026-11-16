# S07 — автономная реализация и фактические проверки

Дата: 2026-11-16. База `main 73db264923a03edaf7c00d46a3951784d59b67e8`, отдельная ветка `feature/s07-observability`, worktree `/workspace/vidharbor-s07-observability`. Состояние старых четырёх рабочих папок, включая index/MERGE_HEAD/dirty/untracked S06, совпало с сохранённым snapshot: [проверка](evidence/s07/workspaces-preserved.json). Принятые S04/S05/S06/S08 не реализовывались повторно; продуктовых миграций S07 нет.

Оригинальный `implementation-plan-s07.md`/`2d65cb9` недоступен: нет локального объекта/ветки/worktree/файла, remote refs S07 отсутствуют, GitHub lookup коммита вернул 422. [Результат поиска](evidence/s07/original-plan-search.txt). Реализован **только явно разрешённый владельцем scope P06–P10**, не выдуманный согласованный план. Получение/сверка оригинала остаются открытыми. Весь S07/S08/Homework 005 не объявляются Done.

## A. Сделано и проверено

| Требование → файлы | Фактическая проверка | Итог / evidence |
|---|---|---|
| T701/P06: [telemetry.py](../../../api/vidharbor/telemetry.py), gunicorn config | JSON timestamp/level/service/event/error_code/технические UUID; не сериализуются message/args/exception text/body/headers/query/user data | PASS:1997 JSON records, чувствительные test title/username/password/cookie/CSRF markers отсутствуют; [полные JSON logs](evidence/s07/final-s07.logs.jsonl) |
| T701/P07: uploads/reprocessing/relay/job_consumer/publication | Correlation=Upload UUID, request_id уникален для HTTP; Outbox сохраняет carrier, Rabbit headers передают дочерний контекст, worker восстанавливает его в thread. Retry сохраняет carrier; manual retry имеет новый HTTP trace с тем же correlation | PASS:7 связанных событий, HTTP/job/Outbox/Rabbit/worker/READY и реальная PostgreSQL retry unit проверка; [отчёт](evidence/s07/final-s07.json) |
| T701/P08: SDK→OTLP→Tempo | Реальные parent span IDs, service resources и ненулевые длительности; поиск связанных logs по trace ID | PASS:6 spans trace `82f77f97135deec8813a64d7c8bab838`; [Tempo trace JSON](evidence/s07/final-s07.trace.json) |
| T702/P09: [metrics.py](../../../api/vidharbor/metrics.py), Prometheus |5 app targets; API count/errors/latency; durable job/video/attempt/duration/retry/outbox; Rabbit ready depth/consumers/DLQ; Delivery states/attempts | PASS:реальные HTTP400, failed job/DLQ, success/READY delta и loopback webhook запись; retry gauge проверен PostgreSQL unit-тестом. [Отчёт](evidence/s07/final-s07.json) |
| T702/P10: [панель](../../../config/observability/dashboards/vidharbor.json), provisioning | Grafana выполняет настоящий datasource query; Chrome входит через форму и рисует графики | PASS:16 queries,0 query errors,0 external browser requests; [браузер](evidence/s07/final-dashboard.json), [screenshot](evidence/s07/final-dashboard.png), [Prometheus frames через Grafana](evidence/s07/grafana-live-query.json) |
| T702: устойчивость observability | Остановка Tempo: новый Job всё равно READY, exporter failures растут, новые traces после restart. Остановка Prometheus: реальная ошибка datasource, затем новые frames. Исходный Job/attempt count/READY event/hash неизменны | PASS: [полный smoke report](evidence/s07/final-s07.json) |
| T702/P04–P05: отказы зависимостей | Broker down: live200/ready503 relay/worker, outbox pending и firing alert; после restart тот же Job и trace завершились. S01 также проверил PG outage | PASS: [S07](evidence/s07/final-s07.json), [S01](evidence/s07/s01-regression.log) |
| T702: private access/retention/rotation | Web metrics404, direct API без bearer401; ports только loopback, no host ports PG/Rabbit/API/OTLP/component. json-file1m×3 на10services; отдельный disposable probe вызвал настоящую rotation | PASS:6000→2155lines/3files; [rotation](evidence/s07/final-rotation.json), [Compose/source boundaries](evidence/s07/compose-source-boundaries.json) |
| T703: существующая регрессия сохранена | Полный Django на PostgreSQL с telemetry on и отдельно off; supervisor real process/flock, restore guards | PASS:83/83 в обоих режимах (22.790с on /24.154с off),3/3 supervisor,5/5 restore. [Django on](evidence/s07/final-django.log), [off](evidence/s07/final-django-default.log), [supervisor](evidence/s07/supervisor.log), [restore](evidence/s07/restore-guards.log) |
| T703: миграции/конфиги/сборка | makemigrations check, migrate check; promtool config/4rules; actionlint all workflows; compile/JSON; web Docker build | PASS: [migrations](evidence/s07/migrations.log), [promtool](evidence/s07/promtool.log), [actionlint exit0](evidence/s07/actionlint.exit), [web cached build](evidence/s07/frontend-build.log). Новый независимый tsc/vite запуск не заявляется: frontend source не менялся |
| T703: safe artifacts | Safe file принят; known credential и unexpected extension отклонены. Новый job nonzero при нарушении, always-cleanup собственных containers/volumes/secrets | PASS локальных guard cases3/3: [artifact safety](evidence/s07/artifact-guards.log); сам GitHub cleanup-run Not Run |
| Offline templates | nginx-t с synthetic cert/no-network/no-ports; disabled Django host/origin/secure cookies/proxy;4 invalid host inputs; synthetic S06 payload schema | PASS только offline: [Nginx](evidence/s07/public-templates.json), [settings/payload](evidence/s07/offline-contract-settings.log) |

Trace: HTTP complete75.636ms →job.create7.500ms →outbox.publish4.350ms →worker.process2250.090ms с ffmpeg.encode2187.378ms и video.publish4.825ms. Сохранены один Job/Attempt/READY event, `sent_at` после публикации, original SHA-256 `340d555f4b4466940c180691788b4f9a980db375fef2434b8f3d1ad2a468f003`. Эти6 spans не представляют все HTTP запроса пользователя: upload/create/content имеют собственные HTTP trace и общий correlation. Очередь видна также по gap timestamps/Outbox age, а не выдаётся за выдуманный отдельный span.

Исходный S06 sender/receiver и immutable video.ready не переписаны. Для webhook metric использован существующий loopback test receiver, одна ReceiverRow; это **не n8n/Google API**. Failure/DLQ fixture принадлежит только этому S07 project: его original временно повреждён намеренно для реального terminal failure и восстановлен из копии в finally. Не запускать этот smoke на production/чужом стенде.

## Команды и конфигурация

В managed cloud вне Git сохранён env/override в `/workspace/.vidharbor-cloud/s07`. Он задаёт уникальные project `vidharbor-s07-local`, порт8120, test DB/secrets и необходимые platform overrides. Основные команды из корня worktree:

```bash
source /workspace/.vidharbor-cloud/s07/env.sh
dc exec -T upload python manage.py test vidharbor --noinput
dc exec -T -e VIDHARBOR_OBSERVABILITY=0 upload python manage.py test vidharbor --noinput
dc exec -T upload python manage.py makemigrations --check --dry-run
dc exec -T upload python manage.py migrate --check
dc exec -T -e PYTHONPATH=/app upload python /review/smoke_s04_supervisor.py
python3 scripts/test_s08.py
python3 scripts/smoke_s07.py --monitoring --fixture /workspace/.vidharbor-cloud/completion/fixtures/landscape.mp4 --report /workspace/.vidharbor-cloud/s07/final-s07.json
/workspace/.vidharbor-cloud/venv/bin/python scripts/smoke_s07_dashboard.py --report /workspace/.vidharbor-cloud/s07/final-dashboard.json
python3 scripts/smoke_s07_rotation.py --report /workspace/.vidharbor-cloud/s07/final-rotation.json
/workspace/.vidharbor-cloud/venv/bin/python scripts/smoke_s01.py
dc exec -T prometheus promtool check config /etc/prometheus/prometheus.yml
dc exec -T prometheus promtool check rules /etc/prometheus/alerts.yml
dc build web
/workspace/.cache/ci-tools/actionlint -shellcheck= .github/workflows/*.yml
git diff --check
```

`dc` — Compose с `.env.example`, base+observability+private cloud override и profile observability. Read-only mounts дают только маленький fixture и private review tools. Temporary secrets не включаются в source/evidence. Для последнего83-test run дополненный test_metrics.py загружен в **свой** test upload container, product modules не менялись; hashes всех app modules совпали с исходниками API/upload. Новая сборка/CI получит этот test file обычным COPY. Результат не переносится на неизвестную сборку.

Последовательность checkpoints: T701 `933745d1940b6a3338a79e21df4b3ac7a292dcaa`; T702 `871426e` после78-test/T701 и82-test+5/5metrics этапных прогонов. Финальный83-test набор включает9 новых diagnostics/metrics tests относительно74 в опубликованной базе. T703/cloud preparation зафиксированы следующим локальным checkpoint; полный финальный SHA приложен к review-пакету вне self-referential документа.

## Ограничения managed cloud и устранённые первичные FAIL

Docker 29.9.2/Compose 2.40.3, storage driver **VFS**, cgroup driver **none**. [Границы среды](evidence/s07/runtime-boundaries.json). Поэтому configured CPU/RAM не являются доказанным enforcement. Замеренный process RSS в одном срезе: Prometheus103895040B, Grafana240013312B, Tempo153800704B; это не peak/RSS-limit test. Grafana cap поднят256→512MiB с запасом относительно измеренного RSS; применение caps на реальном cgroup host ещё Not Run.

Полный Grafana image при VFS исчерпал свободный диск. Удалены только собственные тестовые контейнеры/неудачные новые image artifacts; старые volumes/worktrees сохранены. Очищен только неиспользуемый dangling build cache. Cloud override запускает **полный официальный Grafana 12.4.15 musl distribution** read-only в своём cached Alpine container. Архив проверен по официальному SHA-256; это реальный Grafana server/assets, не заглушка. [Provenance и binary hash](evidence/s07/grafana-runtime-provenance.json). Исходный Compose и новый GitHub workflow используют pinned official Docker image; **именно этот image в финальном cloud stand не проверен**.

Managed overrides также доверяют публичному proxy CA при pip/npm/apk build, делают исходники/config читаемыми service UID, используют loopback Python health без proxy. Исправлены права Tempo config и Grafana internal NO_PROXY; BusyBox wget health требует `-Y off`. Первоначальные dashboard harness FAIL относились к отсутствию формы login и учёта query string `/api/ds/query?...`. Визуальная проверка также обнаружила пустой full-page screenshot из-за виртуализации grid при изменении viewport. Добавлены повторная проверка видимости panel/canvas и его площади, viewport screenshot; новый снимок просмотрен, графики видны. Настоящие rendering/data assertions усилены, не ослаблены. Offline nginx-t сначала обнаружил read-only temp dirs harness; добавлены только ephemeral tmpfs, конфигурация приложения не менялась. Первичные ошибки зафиксированы как ошибки, не как PASS.

JSON formatter intentionally теряет свободный текст сторонних logger messages ради исключения PII/secrets; errors ищутся по service/event/error_code/IDs/status. Tempo1h, Prometheus6h/128MB, logs1m×3; это учебные bounds, не production SLO/полная disk quota. Default startup не требует telemetry secrets и не включает exporter. Queue metric означает ready, **без unacked**. Notifier не включён по умолчанию. Alerts локальные, Alertmanager/внешних каналов нет.

## B. Подготовлено, внешне не проверено

- [Новый workflow](../../../.github/workflows/s07-observability.yml): PR main/workflow_dispatch, GitHub-hosted ubuntu24.04, pinned actions/images, реальные data path/outages/browser/rotation, checked artifact и always-cleanup. Старые Django/S01 workflows byte-identical базе. **GitHub run Not Run**: push/PR не разрешены; ссылок на новый run не существует.
- [Учебный n8n/Sheets пакет](../integration-rehearsal.md): точное raw-body HMAC/ACK/IDs/row, credential-free env, synthetic JSON, сценарии и результаты. Реальная read/check/update сериализация/поздние in-flight операции требуют отдельного выбора и прогона; values.update не CAS.
- [Public topology/HTTPS/ресурсы/update/rollback/checklist](../public-deployment.md) и отключённые config/deployment templates. Сервер/домен/сертификат не созданы, DNS/порты не открывались, deployment не выполнялся.
- [Независимые backup/authority варианты](../backup-restore.md#независимый-носитель-подготовленные-варианты-не-внедрение): риски/стоимость/host-loss prerequisites и RPO/RTO, без назначения provider/retention/targets за владельца. Принятый same-host S08 drill и большой3.53GB результат сохранены, не повторялись.
- [30тем для объяснения](../theory-notes.md): материал подготовлен, понимание/Done/Reviewed не выставлены.

## C. Внешние действия до приёмки

1. Отдельно согласовать публикацию и выполнить GitHub Actions на точном SHA; независимый Review S07, получить и сверить оригинальный plan 2d65cb9.
2. Выбрать/разрешить учебные n8n workflow и Sheets tab, секрет/сериализацию, публичный HLS; выполнить success/retry/lost-response/different-ID row conflict/delete-in-flight/restart/restore reconciliation. FR-011/012, AC-007, T601–603/T503 открыты.
3. Выбрать сервер/домен/TLS и ресурсы; после отдельного разрешения public deployment и end-to-end «загрузка→очередь→FFmpeg→просмотр»,3–5GB под **действующими** CPU/RAM caps, публичный доступ/изоляция/renewal. Локальный успешный путь не является серверным демо.
4. Утвердить независимый carrier/authority, encryption keys, retention/частоту/бюджет, RPO/RTO/предел паузы/A1–A6; внедрение только после решения, независимый host-loss drill и внешняя сверка Sheets до notifier.
5. Финальная RTM/AC-009/Review, Production Practice KazanCourts, Level 5 A/B/C/CP5, Review 01, Homework Defense/ментор/Learning Pack отдельно. Файлы и локальные PASS не закрывают Homework 005.

[Manifest evidence с SHA-256](evidence/s07/manifest.json). В archive нет secrets, cookies/session state, видео, dump/media bundles или Grafana binaries. Полный diff относительно73db264 и финальный HEAD формируются вне repo для независимого Review. Push/PR/merge/deployment/реальные n8n/Sheets/внешнее хранилище в этой задаче не выполнялись.
