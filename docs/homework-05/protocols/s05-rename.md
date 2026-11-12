# S05 · Переименование и права владельца

12 ноября 2026 года. Ветка `feature/s05-rename`, база `origin/main` / `f120ac2` (S03). Проверена самостоятельная часть T501; весь этап S05 не завершён. [План](../../implementation-plan-s05.md).

## Изменение

`POST /api/videos/{video_id}/rename`, JSON `{"title":"Новое название"}`. Требуются сессия владельца и CSRF. Успех — 200 с `id`, `title`, `state`; неверное название — 400, отсутствие сессии — 401, CSRF — 403, чужой или отсутствующий ID — 404. Разрешено 1–200 символов после обрезки пробелов, NUL запрещён. Меняется только Video.title под блокировкой строки, без миграций, новой обработки и изменения исходной Upload.

Кабинет позволяет открыть форму, сохранить или отменить изменение. Ошибка сохраняет введённое значение для повтора. Название выводится текстом.

## Проверки

Отдельный Compose-проект `vidharbor-s05`, тестовые тома, порт 8086. PostgreSQL 16, RabbitMQ 4.2 и FFmpeg из закреплённых в проекте образов. API и upload собраны из текущего кода.

Особенности облачной среды вынесены за checkout в `/workspace/vidharbor-cloud`: Dockerfile для сборки монтирует системный CA для pip/npm/apk и разрешает чтение скопированного кода UID 65532. Исходные Dockerfile не менялись. BusyBox wget из healthcheck использовал облачный proxy для локального запроса и получал 403; локальный override `s05-health.json` заменил эту проверку на Python urllib. API напрямую отвечал 200. Эти настройки не меняют логику приложения.

Выполненные команды из корня репозитория:

```sh
VIDHARBOR_PORT=8086 COMPOSE_PARALLEL_LIMIT=2 docker compose -p vidharbor-s05 --env-file .env.example -f compose.yaml -f /workspace/vidharbor-cloud/compose.json build api upload web migrate
VIDHARBOR_PORT=8086 docker compose -p vidharbor-s05 --env-file .env.example -f compose.yaml -f /workspace/vidharbor-cloud/s05-health.json up -d --wait --wait-timeout 120 db rabbitmq migrate api upload web
docker compose -p vidharbor-s05 --env-file .env.example run --rm --no-deps upload python manage.py test vidharbor -v 2
docker compose -p vidharbor-s05 --env-file .env.example run --rm --no-deps api python manage.py makemigrations --check --dry-run
COMPOSE_PROJECT_NAME=vidharbor-s05 VIDHARBOR_PORT=8086 VIDHARBOR_CHROME=/usr/bin/chromium python3 scripts/smoke_s05_rename.py
curl -fsS --max-time 5 http://127.0.0.1:8086/health/ready
```

| Проверка | Результат |
|---|---|
| TypeScript/Vite, `npm run build` из `web/` | PASS |
| Django `test vidharbor` | PASS: 14 тестов, включая 5 новых RenameVideoTests |
| Миграции на новой тестовой базе | PASS: тестовая БД создана, миграции применены, БД удалена после тестов |
| `makemigrations --check --dry-run` | PASS: No changes detected |
| Сохранение ID, поколения, Upload, Job и Outbox при повторе переименования | PASS: сравнение записей в тестах |
| Два автора, чужой прямой запрос, сессия, CSRF | PASS: Django и браузерный сценарий |
| Пустое/нестроковое/длинное название, NUL, неверное тело запроса, граница 200 символов | PASS: Django |
| Сохранение и перезагрузка, отмена, ошибка 503 и повтор с сохранённым текстом | PASS: Chromium/Playwright, реальный API; 503 внедрён маршрутом браузера |
| HTML в названии не создаёт DOM-элемент и не выполняется | PASS: Chromium |
| `/health/ready` | 200, postgres=true, rabbitmq=true |
| Каталог, поиск, прежняя ссылка и воспроизведение | Not Run: требуют S04 |
| Ручной повтор, удаление и гонки FFmpeg | Not Run: запланированы после S04 |
| Отмена внешнего уведомления / восстановление старой БД | Not Run: после T602 / в T802 |

Браузерный сценарий создаёт два тестовых аккаунта и QUEUED-видео с префиксом S05 прямо в тестовой БД. Физического оригинала у этой браузерной фикстуры нет: проверяются переименование и кабинет, а не загрузка или публикация. Скрипт оставляет фикстуры для осмотра; запускать только на одноразовом тестовом проекте. Тесты сохранения оригинала проверяют неизменность его метаданных; фактическая целостность HLS и ссылки остаётся проверкой S04.
