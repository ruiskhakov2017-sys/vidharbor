# Level 5: материал для объяснения, не отметка об усвоении

30 тем из таблицы A01–A12/B01–B06/C01–C12 [RTM](requirements-traceability.md#теория-и-общий-учебный-цикл). Для всех статус **подготовлен материал; понимание/Defense/менторский Review не проверены**. Это не замена оригинального roadmap или отсутствующего Review 01. Пример VidHarbor не закрывает Production Practice KazanCourts/F15, CP4/CP5, Homework Defense или Learning Pack.

| ID / тема | Краткое объяснение | Практика / вопрос для защиты |
|---|---|---|
| A01 Image/container | Image — неизменяемый шаблон слоёв; container — процесс с writable layer и настройками | [Dockerfile](../../api/Dockerfile): почему пересоздание API не должно удалять original? |
| A02 Volumes | Volume хранит данные независимо от контейнера, но не от физического хоста | [backup](backup-restore.md): чем down отличается от down --volumes и от backup? |
| A03 Networking | Compose DNS и приватная сеть связывают сервисы; host publish создаёт иной путь доступа | [Compose](../../compose.yaml): почему api обращается к db, а браузер к loopback web? |
| A04 Compose | Декларация services, dependencies, secrets, volumes и health; project name изолирует ресурсы | Показать [operations](operations.md): что разделить двум параллельным стендам? |
| A05 VPS/PaaS/Cloud | VPS даёт ОС и обязанности администратора; PaaS управляет частью runtime; cloud — модель предоставления ресурсов | [public prep](public-deployment.md): почему временный Actions runner не постоянный demo? |
| A06 Reverse proxy | Edge принимает HTTP/TLS и передаёт запросы внутренним процессам; streaming зависит от buffering/timeouts | [Nginx template](../../config/deployment/nginx-https.conf.example): где выключена буферизация upload? |
| A07 DNS | A/AAAA связывают имя с адресом; TTL влияет на время переключения | Почему AAAA при неработающем IPv6 ломает часть клиентов? Публичная практика Not Run |
| A08 TLS/HTTPS | TLS шифрует канал и проверяет hostname/цепочку сертификата; нужны renewal и защита key | Почему nginx -t с self-signed cert не подтверждает публичный HTTPS? |
| A09 Environments | Dev/test/demo/prod различаются данными, секретами и доступами, даже если используют общий код | Почему smoke с отключением broker нельзя направить на рабочий demo? |
| A10 Secrets | Credential — закрытые данные вне source/logs; ротация должна учитывать consumers и backup | Почему нельзя просто заменить postgres_password у заполненного PG volume? |
| A11 Migrations | Миграция меняет схему/данные, поэтому требует совместимости и проверки на копии | [S08](protocols/s05-s08-completion.md): что проверено на0003→0006 и что не доказывает makemigrations --check? |
| A12 Rollback | Возврат приложения безопасен только при совместимой схеме и учёте новых данных/внешних эффектов | Почему старый dump без fresh deletion authority опасен? |
| B01 Build | Build превращает source+закреплённые зависимости в образ/статику; cache переиспользует одинаковые входы | [web Dockerfile](../../web/Dockerfile): где TypeScript/Vite и что означает CACHED? |
| B02 Static checks | Lint/parser находят часть ошибок без полноценного запуска; они не заменяют интеграцию | Чем actionlint/compileall отличаются от реального Actions run? |
| B03 Tests | Unit изолирует логику, integration проверяет реальные границы, browser проверяет пользовательский путь | [S07 protocol](protocols/s07-observability.md): где Mock, где настоящий PG/Rabbit/Tempo? |
| B04 Artifacts | Проверяемый результат включает source SHA, diff, версии, логи и hashes; secret files артефактом не являются | Почему зелёный лог без SHA и условий недостаточен? |
| B05 Deployment | Deployment запускает проверенную сборку в конкретной среде с данными/доступом и подтверждением здоровья | Почему push/CI сами по себе не означают разрешение deployment? |
| B06 Rollback strategy | Заранее определённые compatibility/gates/backup дают безопасный путь при ошибке релиза | Когда можно вернуть только image, а когда нужен clean restore? |
| C01 Structured logs | JSON с фиксированной схемой можно фильтровать машинно; allowlist снижает риск утечек | [telemetry.py](../../api/vidharbor/telemetry.py): почему не сериализуется exception message? |
| C02 Metrics | Агрегированные ряды описывают число/длительность/состояние; labels должны иметь ограниченную кардинальность | [metrics.py](../../api/vidharbor/metrics.py): почему video_id/title не labels? |
| C03 Traces | Trace — связанные spans с длительностями и parent IDs между процессами | Показать реальный HTTP→Outbox→worker trace; почему один UUID в log не является trace? |
| C04 Correlation IDs | Correlation связывает бизнес-операции, даже если у разных HTTP/retry разные traces | Почему correlation=Upload UUID, а новый HTTP retry имеет другой request_id? |
| C05 Dashboards | Панель делает реальные запросы к источнику метрик; JSON provision без данных не доказательство | Показать [dashboard](../../config/observability/dashboards/vidharbor.json) и outage datasource→recovery |
| C06 Alerts | Rule вычисляет условие и for-интервал; доставка уведомления требует отдельного канала | [rules](../../config/observability/alerts.yml): почему firing без Alertmanager не означает отправленный pager? |
| C07 Health/readiness | Liveness показывает живой процесс, readiness — способность выполнять работу; зависимость может быть недоступна при live 200 | Почему worker при broker down отвечает live 200/ready 503? |
| C08 Backups | Согласованная копия требует границы данных, полноты, целостности и независимого хранения | Почему dump отдельно от media и authority может воскресить удаление? |
| C09 Restore | Restore проверяется в чистой отдельной БД/volumes с бизнес-инвариантами до открытия доступа | Почему успешный pg_restore ещё не PASS всего восстановления? |
| C10 Graceful shutdown | Остановка должна ограничивать время, корректно останавливать writers и сохранять возможность повторной доставки | [supervisor probe](../../scripts/smoke_s04_supervisor.py): зачем process group и сохранённый flock inode? |
| C11 RPO/RTO | RPO ограничивает потерю данных, RTO — время безопасного возврата сервиса | Почему измеренные106.147 с restore не являются полным целевым RTO? |
| C12 SLI/SLO | SLI — измеряемое качество за окно; SLO — согласованная цель для него | Предложить SLI доли успешных upload→READY; почему произвольный alert threshold не production SLO? |

Для проверки понимания: объяснить каждую тему своими словами, найти указанный факт/файл, разобрать отказ и компромисс; записать ответы и вопросы. Только независимая проверка владельца/ментора позволяет менять статусы понимания. Теория подготовлена, за пользователя ответы не засчитаны.
