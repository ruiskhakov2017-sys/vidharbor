from django.core.management.base import BaseCommand

from vidharbor.metrics_sampler import serve


class Command(BaseCommand):
    help = 'Serve cached private metrics; collectors run in deadline-limited child processes.'

    def handle(self, *args, **options):
        serve()
