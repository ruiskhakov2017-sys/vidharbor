"""One disposable, read-only collection; caller enforces the total wall deadline."""
from django.core.management.base import BaseCommand
from django.db import connection
from prometheus_client import CollectorRegistry, generate_latest

from vidharbor.metrics import DatabaseCollector


class Command(BaseCommand):
    # The disposable read-only collector must not import/check every HTTP view
    # on each sample. Full system checks run at startup and in the Django job.
    requires_system_checks = []

    def handle(self, *args, **options):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '250ms'")
                cursor.execute("SET lock_timeout = '100ms'")
                cursor.execute("SET default_transaction_read_only = on")
                cursor.execute("SET application_name = 'vidharbor-metrics'")
            registry = CollectorRegistry(auto_describe=False)
            registry.register(DatabaseCollector())
            self.stdout.write(generate_latest(registry).decode(), ending='')
        finally:
            connection.close()
