# Протокол S01 · эксперимент T103 с RabbitMQ

Дата: 10 ноября 2026 года. Итог: все четыре сценария T103 прошли за два запуска. Идентификатор первого запуска: `vidharbor-t103-4f082acd`.

## Стенд

- Docker Engine 29.2.0 в Linux-режиме. Временный RabbitMQ 4.2.9 (образ `rabbitmq:4.2-alpine`, digest `sha256:dea0f5843025e0c3c1a128b14fc2e181ea59ff369e7048e6a7fbac83bd8c4f9d`, лимит 512 МиБ и 1 CPU).
- Временный Linux-контейнер исполнителей (Python 3.13.18, Pika 1.3.2, FFmpeg 8.1.3, лимит 384 МиБ и 1 CPU). Команда эксперимента — `docker exec vidharbor-t103-4f082acd-runner python /tmp/experiment.py`. Порты на Windows не публиковались. `/stage/processing` находится на выделенном Docker volume.
- По одному сообщению с фиксированным ID в двух разных quorum-очередях. Публикация с подтверждением брокера, получение с `auto_ack=False`. Повторного publish после аварии не было. При закрытии соединения неполучившее ack сообщение возвращается в очередь. [Документация RabbitMQ](https://www.rabbitmq.com/docs/4.2/confirms#automatic-requeueing).
- Первая попытка не дошла до сценариев из-за таймаута подготовки инструментов. Её ресурсы удалены. Успешный запуск подготовлен с локальным колесом Pika и отдельной установкой FFmpeg.

## Сценарий 1. Падение до ack

Запустил первый Python-обработчик для `case1-job-01`. Он получил сообщение с `redelivered=false` и ждал, не вызывая ack. Отправил процессу PID 47 сигнал `SIGKILL`. Код завершения -9.

RabbitMQ повторно выдал тот же ID и payload новому процессу PID 49. У новой доставки был `redelivered=true`. Она наблюдалась через 788 мс после остановки старого процесса. Подтверждение отправил только новый обработчик. После ack готовых сообщений в очереди 0, контрольный `basic.get` ничего не вернул.

## Сценарий 2. Повторная выдача при живом FFmpeg

Первый обработчик получил `case2-job-01`, захватил исключительную файловую блокировку `/stage/case2.lock` и запустил FFmpeg PID 55 с наследуемым дескриптором 7. FFmpeg в реальном времени кодировал 18 секунд синтетического видео 160×90, записывая короткие сегменты в `/stage/processing`. После начала записи отправил обработчику PID 53 сигнал `SIGKILL`, пока FFmpeg работал. Процесс FFmpeg сохранил открытый дескриптор блокировки.

RabbitMQ повторно выдал то же задание через 611 мс. Новый обработчик PID 69 увидел `redelivered=true`, попытался захватить тот же lock и стал ждать. В это время число сегментов старого процесса выросло с 2 до 3, а их суммарный размер — с 16168 до 32524 байт. У нового процесса было 0 файлов.

Через 16.392 с ожидания новый обработчик захватил lock и проверил, что старый FFmpeg больше не держит его дескриптор. Только после этого он запустил FFmpeg PID 71 в **той же** папке. Старый процесс оставил 18 сегментов, новый записал 3. Новый FFmpeg завершился с кодом 0, затем обработчик подтвердил сообщение. Очередь пуста. Параллельной записи в проверенном сценарии не было.

## Завершение и границы

Эксперимент завершился с кодом 0. Оба временных контейнера остановлены и удалены. Временная сеть и том удалены. Проверка по метке запуска показала 0 контейнеров, 0 томов и 0 сетей. Образы Docker сохранены в локальном кеше.

Тест проверяет аварийное завершение отдельного процесса в Linux и наследование блокировки FFmpeg на Docker volume. В клиенте настроен heartbeat 15 секунд и обслуживается AMQP-соединение во время ожидания. Проверки принудительного истечения heartbeat и таймаута ack приведены ниже. Остановка Docker-хоста, работа приложения и восстановление публикации не проверялись.

## Дополнение. Heartbeat и таймаут подтверждения

Дата: 10 ноября 2026 года. Запуск `vidharbor-t103-timeouts-ab5bd016`. Использованы те же RabbitMQ 4.2.9 и Python 3.13.18 с Pika 1.3.2. Две отдельные quorum-очереди, по одному сообщению с ручным подтверждением. Порты на Windows не публиковались. Предыдущие сценарии не запускались. Параметры лаборатории — heartbeat 6 с и `consumer_timeout=60000` мс. Фактическое значение на брокере подтверждено `rabbitmqctl eval`: `{ok,60000}`.

Время в таблице — МСК (UTC+3). В клиентском журнале и журнале RabbitMQ сохранено время UTC.

| Проверка | Как вызван сбой | Закрытие по журналу RabbitMQ | Повторная выдача |
|---|---|---|---|
| Пропали сигналы heartbeat | После получения задания в 15:25:49.801 отправлен `SIGSTOP` процессу PID 54. Состояние `T` и открытые сокетные дескрипторы подтверждены. Процесс оставался приостановленным при повторной выдаче. | **15:26:07.538**. RabbitMQ закрыл соединение `vidharbor-t103-timeouts-ab5bd016.heartbeat.old` с причиной `missed heartbeats from client, timeout: 6s`. | **15:26:07.754**, `t103-heartbeat-job-01`, `redelivered=true`. По монотонным часам прошло 17,970 с от паузы. |
| Подтверждение просрочено при живой связи | Канал 1 получил задание в 15:26:08.415. Обработчик не отправлял `ack`, продолжал обслуживать AMQP и выполнил 30 контрольных запросов по каналу 2 того же соединения. | **15:28:08.374**. В журнале указаны очередь, канал 1, delivery tag 1, просрочка подтверждения и `precondition_failed`. Клиент получил `406 PRECONDITION_FAILED`. Сразу после этого контрольный запрос по каналу 2 того же соединения прошёл. | **15:28:08.620**, `t103-ack-timeout-job-01`, `redelivered=true`. По монотонным часам прошло 120,231 с от первой доставки. |

При таймауте ack клиент увидел закрытие канала через 119,989 с после доставки. RabbitMQ 4.2 проверяет просрочку примерно раз в минуту, поэтому настройка 60 000 мс и фактическое время обнаружения отличаются. [Документация RabbitMQ о таймауте подтверждения](https://www.rabbitmq.com/docs/4.2/consumers#acknowledgement-timeout).

В обоих случаях новый обработчик подтвердил повторно полученное задание. Число готовых сообщений после подтверждения — 0. Запуск завершился с кодом 0. Оба контейнера и выделенная сеть удалены, временный том брокера проверен, оставшихся ресурсов стенда — 0.

## Команды и строки журналов

Рабочие команды запуска обоих экспериментов:

```powershell
docker exec vidharbor-t103-4f082acd-runner python /tmp/experiment.py
docker exec vidharbor-t103-timeouts-ab5bd016-runner python /tmp/timeouts.py
docker exec vidharbor-t103-timeouts-ab5bd016-rabbit rabbitmqctl -q eval 'application:get_env(rabbit, consumer_timeout).'
docker logs --timestamps vidharbor-t103-timeouts-ab5bd016-rabbit
```

Команда первого FFmpeg в сценарии записи:

```text
ffmpeg -hide_banner -nostdin -loglevel error -re -f lavfi -i testsrc2=size=160x90:rate=5 -t 18 -an -c:v libx264 -threads 1 -preset ultrafast -g 5 -sc_threshold 0 -f segment -segment_time 1 -segment_format mpegts -reset_timestamps 1 /stage/processing/case2-old-%03d.ts
```

Ключевые события исполнителей:

```jsonl
{"event": "WORKER_KILLED_BEFORE_ACK", "exit": -9, "ms": 1301971, "pid": 47, "role": "case1-old"}
{"case": "case1", "event": "SCENARIO_PASS", "ms": 1302947}
{"event": "WORKER_KILLED_BEFORE_ACK", "exit": -9, "ms": 1305684, "pid": 53, "role": "case2-old"}
{"event": "OLD_WRITES_WHILE_NEW_WAITS", "ms": 1306642, "new_files": 0, "old_bytes_after": 32524, "old_bytes_before": 16168, "old_files_after": 3, "old_files_before": 2, "role": "case2-new"}
{"case": "case2", "event": "SCENARIO_PASS", "lock_wait_ms": 16392, "ms": 1325695, "new_final": {"bytes": 50760, "files": 3}, "old_final": {"bytes": 312268, "files": 18}}
{"case": "heartbeat", "event": "WORKER_SIGSTOP_SOCKET_OPEN", "id": "t103-heartbeat-job-01", "ms": 2045960, "open_socket_fds": 3, "pid": 54, "state": "T", "utc": "2026-11-10T12:25:49.801+00:00"}
{"case": "heartbeat", "delivery_tag": 1, "event": "REDELIVERED", "id": "t103-heartbeat-job-01", "ms": 2063930, "redelivered": true, "utc": "2026-11-10T12:26:07.754+00:00"}
{"case": "ack-timeout", "channel": 1, "code": 406, "connection": "vidharbor-t103-timeouts-ab5bd016.ack-timeout.old", "connection_open": true, "event": "ACK_CHANNEL_CLOSED_BY_BROKER", "id": "t103-ack-timeout-job-01", "ms": 2184580, "reason": "PRECONDITION_FAILED - delivery acknowledgement on channel 1 timed out. Timeout value used: 60000 ms. This timeout value can be configured, see consumers doc guide to learn more", "successful_probes": 30, "utc": "2026-11-10T12:28:08.378+00:00"}
{"case": "ack-timeout", "connection": "vidharbor-t103-timeouts-ab5bd016.ack-timeout.old", "event": "AMQP_PROBE_AFTER_CHANNEL_CLOSE_OK", "ms": 2184585, "probe_channel": 2, "utc": "2026-11-10T12:28:08.383+00:00"}
{"case": "ack-timeout", "delivery_tag": 1, "event": "REDELIVERED", "id": "t103-ack-timeout-job-01", "ms": 2184822, "redelivered": true, "utc": "2026-11-10T12:28:08.620+00:00"}
```

Причины закрытия в журнале RabbitMQ:

```text
2026-11-10T12:26:07.541280765Z [38;5;160m2026-11-10 12:26:07.538119+00:00 [error] <0.1213.0> missed heartbeats from client, timeout: 6s[0m
2026-11-10T12:28:08.377028182Z [38;5;214m2026-11-10 12:28:08.373406+00:00 [warning] <0.1297.0> Consumer 't103-ack-timeout-old' on channel 1 and queue 'vidharbor-t103-timeouts-ab5bd016.ack-timeout' in vhost '/' has timed out waiting for a consumer acknowledgement of a delivery with delivery tag = 1. Timeout used: 60000 ms. This timeout value can be configured, see consumers doc guide to learn more[0m
2026-11-10T12:28:08.379579722Z [38;5;160m2026-11-10 12:28:08.374386+00:00 [error] <0.1297.0> operation none caused a channel exception precondition_failed: delivery acknowledgement on channel 1 timed out. Timeout value used: 60000 ms. This timeout value can be configured, see consumers doc guide to learn more[0m
```
