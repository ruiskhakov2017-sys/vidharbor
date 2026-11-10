# VidHarbor

Учебный видеосервис для Homework 005. Автор загружает исходник, фоновый исполнитель готовит HLS, зритель открывает ролик без регистрации. n8n записывает сведения о публикации в Google Таблицу.

**Стек:** Vue + TypeScript, Django + Django REST Framework, PostgreSQL, RabbitMQ, собственный Python worker, FFmpeg, HLS/hls.js, Nginx, n8n/Google Sheets, Docker Compose, OpenTelemetry, Prometheus, Grafana и Tempo. Для проверок запланированы pytest, Playwright и GitHub Actions.

**Текущий этап:** документальный baseline и Plan Review. DEC-003 принят. Gate 3 открыт по проверкам ресурсов, вертикального критерия и отказоустойчивости. Gate 4 ожидает принятия плана. Команды запуска появятся после проверенного Compose. В этом baseline нет кода приложения.

| Артефакт | Вход / статус | Действие |
|---|---|---|
| [Product Spec](docs/product-spec.md) | В архиве версия 0.2 | Обновлена до 0.3 по DEC-003 |
| [Architecture](docs/homework-05/design.md) | Предыдущее предложение в текстовом виде | Подготовлен актуальный arc42 |
| [Decision log](docs/homework-05/decision-log.md) | DEC-001–003 в архиве | Сохранена история, добавлен DEC-004 |
| [Implementation Plan](docs/implementation-plan.md) | Готового плана в архиве не было | Draft для review |
| [Tasks](tasks.md) | Рабочего списка в архиве не было | Зависимости и ожидаемые результаты |
| [Traceability](docs/homework-05/requirements-traceability.md) | ID требований в локальных источниках | Связь решений, задач и проверок |
| [Reliability](docs/homework-05/reliability.md) | Решения и обсуждения в архиве | Draft контрактов и отказов |
| README и Git ignore | Входы без проектного репозитория | Базовые документы и исключения |
| production-practice, testing-report, backup-restore, operations | Доказательства ещё предстоит получить | В `docs/homework-05/` по итогам проверок |
| theory-notes, Defense 005, Learning Pack 005 | Общий учебный цикл | По регламенту и результатам приёмки |

Локальные входы: каноническое ДЗ №005 ред. 2, практическое ТЗ, AI Native Engineering, основной регламент ментора, Markdown roadmap, исторические Progress/Knowledge Gaps, Learning Pack Addendum, PRD 0.2, DEC-003 и прежнее предложение. В репозиторий входят проектные документы. Казанские корты и теория учитываются в общей работе отдельно.
