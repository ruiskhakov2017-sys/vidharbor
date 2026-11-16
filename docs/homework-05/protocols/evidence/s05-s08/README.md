# Evidence учебного прогона S05/S06/S08

Полный контекст и ограничения — в [сводном протоколе](../../s05-s08-completion.md). Это выбранные текстовые логи и JSON результатов. У `.log` удалены только пробелы/табуляции в конце строк для `git diff --check`; строки/ошибки не исключались. SHA-256 исходных и review-файлов записаны в [manifest](evidence-sha256.json). Сырые оригиналы остались вне Git в `/workspace/.vidharbor-cloud/completion`.

`results.json` фиксирует exit codes отдельных реальных прогонов, а не общий безусловный PASS этапов. Ошибочные первичные прогоны Nginx/Firefox сохранены отдельно и не считаются успешными. Backup/media/pg_dump/RabbitMQ archives, cookies/session и файлы секретов сюда не включены. `backup-summary.json` и `restore-state.json` содержат только метаданные, не саму резервную копию. Текущая ветка не отправлялась на GitHub: нового Actions run нет.
