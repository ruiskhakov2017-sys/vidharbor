from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from vidharbor.test_receiver import receiver


class Command(BaseCommand):
    help = "Run isolated loopback TEST receiver backed by ReceiverRow, NOT Google Sheets."

    def add_arguments(self, parser):
        parser.add_argument("--secret-file", required=True)
        parser.add_argument("--port", type=int, default=8096)

    def handle(self, *args, **options):
        try:
            secret = Path(options["secret_file"]).read_bytes().strip()
            server = receiver(secret, ("127.0.0.1", options["port"]))
        except (OSError, ValueError):
            raise CommandError("Check test receiver secret file and loopback port.") from None
        self.stdout.write("TEST receiver listening on loopback; PostgreSQL rows only.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
