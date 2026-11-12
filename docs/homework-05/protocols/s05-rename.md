# S05 · Переименование и права владельца

12 ноября 2026 года. Ветка `feature/s05-rename`. Проверявшийся код: `cb0fe48fdff521dd2e35ec4fc529a1eb0f3b36f0`; база S03: `f120ac27ba8541bfdbc3af424e287f0662a9890f`. Самостоятельная часть T501.1 (API/кабинет) предварительно принята независимым review по представленным доказательствам. Публичная интеграция T501.1, T501.2, весь T501, AC-005 и S05 не завершены. Согласована публикация проверенной части T501.1 и документации через PR; merge требует отдельного разрешения; ранее опубликованная ветка `review/s05-cb0fe48` содержит только ZIP/TXT для рассмотрения. [План](../../implementation-plan-s05.md).

## Изменение

`POST /api/videos/{video_id}/rename`, JSON `{"title":"Новое название"}`. Требуются сессия владельца и CSRF. Успех — 200 с `id`, `title`, `state`; неверное название — 400, отсутствие сессии — 401, CSRF — 403, чужой или отсутствующий ID — 404. Разрешено 1–200 символов после обрезки пробелов, NUL запрещён. Меняется только Video.title под блокировкой строки, без миграций, новой обработки и изменения исходной Upload.

Кабинет позволяет открыть форму, сохранить или отменить изменение. Ошибка сохраняет введённое значение для повтора. Название выводится текстом.

## Исходный прогон и доказательства

Результаты ниже относятся к исходному прогону реализации, а не к текущей корректировке документации. Тесты при этой корректировке повторно не запускались. Журналы Django и браузера сохранены в `/tmp/s05-tests.log`, `/tmp/s05-browser.log` и в ZIP (`_review/django-tests.log`, `_review/browser-tests.log`); сборка — в `/tmp/s05-build.log`. Вывод `makemigrations` и HTTP-ответ `ready` сохранены только в истории чата, отдельного файла журнала нет. Версии Chromium и Playwright именно исходного прогона в журналах не зафиксированы.

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

## Реальные компоненты и имитации

- RenameVideoTests: настоящий Django-обработчик и PostgreSQL через Django Client; сессия создаётся force_login, включая отдельный тест отсутствия сессии и проверку CSRF. Сравниваются записи Video, Upload, Job и Outbox; файл оригинала не создаётся.
- Chromium/Playwright: настоящие интерфейс, HTTP API, регистрация/вход двух авторов и PostgreSQL. Видео QUEUED и Upload COMPLETE создаются напрямую в БД; физического оригинала и публикации нет. Ответ 503 один раз подставлен маршрутом Playwright, а не вызван сбоем API. Проверены сохранение, перезагрузка, отмена, повтор формы после ошибки, безопасный вывод HTML, чужой запрос и CSRF.
- Регрессия: 3 UploadTests, 3 JobAttemptTests и 3 RelayTests входят в общий результат 14 тестов вместе с 5 RenameVideoTests. В UploadTests запускаются реальные FFmpeg/ffprobe и файловые операции, но отдельные сбои outbox и нехватки места внедряются через patch. В RelayTests канал RabbitMQ заменён Mock; JobAttemptTests проверяют логику состояний в БД без сквозной обработки worker.
- RabbitMQ на стенде настоящий; ready проверяет доступ к зависимостям. Результат не доказывает сквозную публикацию/доставку задания S05 через реальный брокер.
- Байтовая целостность оригинала при переименовании отдельно не проверялась. Проверена неизменность его метаданных; прежняя ссылка, HLS и переименование READY-видео не проверены до S04.
- Не выполнялись в этом прогоне: полный smoke S01/S02, GitHub Actions, миграция копии БД предыдущего этапа, ручной повтор обработки, удаление, гонки и восстановление очистки. Внешняя отмена уведомления ожидает T602, восстановление старой БД с журналом удалений — T802.

Браузерный скрипт оставляет фикстуры для осмотра. Запускать его только на одноразовом тестовом проекте.

## Воспроизведение без готовых внешних файлов

Ниже инструкция для будущего прогона, а не новый результат PASS. Нужны Linux, доступный Docker с Compose/BuildKit, Python 3 с venv, Node.js 24/npm, Chromium и доступ к закреплённым образам и реестрам зависимостей. В облачной среде сохранить унаследованные proxy и доверие CA; системный bundle `/etc/ssl/certs/ca-certificates.crt` должен включать CA среды. TLS и проверку целостности не отключать.

Выполнить из корня checkout с кодом переименования в Bash. Генератор создаёт аналоги прежних внешних Dockerfile и двух override в одном compose.json вне репозитория. Новый каталог и секреты предназначены для нового одноразового проекта; при повторе на тех же томах сохранить прежние каталог, имя проекта и секреты. При конфликте имени проекта или порта выбрать свободные значения до первого запуска.

```bash
set -euo pipefail
export S05_HELPERS="$(mktemp -d /tmp/vidharbor-s05-review.XXXXXX)"
export COMPOSE_PROJECT_NAME=vidharbor-s05-review
export VIDHARBOR_PORT=8086
export VIDHARBOR_SECRETS_DIR="$S05_HELPERS/secrets"
python3 - <<'PY'
import json, os, secrets
from pathlib import Path

repo = Path.cwd()
helper = Path(os.environ['S05_HELPERS'])
for component in ('api', 'web'):
    text = (repo / component / 'Dockerfile').read_text()
    for old, new in (
        ('RUN pip install', 'RUN --mount=type=secret,id=proxy_ca,required=true PIP_CERT=/run/secrets/proxy_ca pip install'),
        ('RUN npm ci', 'RUN --mount=type=secret,id=proxy_ca,required=true NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca npm ci'),
        ('RUN apk add', 'RUN --mount=type=secret,id=proxy_ca,required=true SSL_CERT_FILE=/run/secrets/proxy_ca apk add'),
    ):
        text = text.replace(old, new)
    text = text.replace('USER 65532:65532', 'RUN chmod -R a+rX /app\nUSER 65532:65532', 1)
    (helper / f'{component}.Dockerfile').write_text(text)
services = {}
for name in ('api', 'upload', 'migrate', 'web'):
    component = 'web' if name == 'web' else 'api'
    services[name] = {'build': {'dockerfile': str(helper / f'{component}.Dockerfile'), 'secrets': ['proxy_ca']}}
    if name in ('api', 'upload'):
        services[name]['healthcheck'] = {'test': ['CMD', 'python', '-c', "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=5).close()"]}
(helper / 'compose.json').write_text(json.dumps({'services': services, 'secrets': {'proxy_ca': {'file': '/etc/ssl/certs/ca-certificates.crt'}}}, indent=2))
secret_dir = Path(os.environ['VIDHARBOR_SECRETS_DIR'])
secret_dir.mkdir(mode=0o700)
for name in ('django_secret_key', 'postgres_password', 'rabbitmq_password'):
    path = secret_dir / name
    with path.open('x') as output:
        output.write(secrets.token_hex(32))
    path.chmod(0o444)
PY
s05_compose=(docker compose --env-file .env.example -f compose.yaml -f "$S05_HELPERS/compose.json")
COMPOSE_PARALLEL_LIMIT=2 "${s05_compose[@]}" build api upload web migrate
"${s05_compose[@]}" up -d --wait --wait-timeout 240 db rabbitmq migrate api upload web
"${s05_compose[@]}" run --rm --no-deps upload python manage.py test vidharbor -v 2
"${s05_compose[@]}" run --rm --no-deps api python manage.py makemigrations --check --dry-run
(cd web && npm ci --cache "$S05_HELPERS/npm-cache" --no-audit --no-fund && npm run build)
python3 -m venv "$S05_HELPERS/venv"
"$S05_HELPERS/venv/bin/pip" install 'playwright==1.62.0'
VIDHARBOR_CHROME="$(command -v chromium)" "$S05_HELPERS/venv/bin/python" scripts/smoke_s05_rename.py
curl -fsS --max-time 5 "http://127.0.0.1:$VIDHARBOR_PORT/health/ready"
```

Playwright 1.62.0 выбран по установленному пакету текущей среды; это не подтверждает версию исходного прогона. Если Chromium имеет другое имя, явно указать его абсолютный путь в VIDHARBOR_CHROME. Перед новым прогоном зафиксировать версии инструментов и SHA кода; результаты, число выполненных тестов и коды выхода записать отдельно. Недоступные зависимости или сбой шага означают остановку и диагностику, а не PASS. Генератор и последовательность в этой инструкции при текущей правке документации не исполнялись.
