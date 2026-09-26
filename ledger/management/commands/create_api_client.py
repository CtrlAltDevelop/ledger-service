from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from ledger.services.clients import create_client


class Command(BaseCommand):
    help = "Register an API client and print its bearer token, which is shown only once."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("name", help="A unique, human-readable name for the client.")

    def handle(self, *args: Any, **options: Any) -> None:
        client, token = create_client(options["name"])
        self.stderr.write(f"created client {client.name} ({client.id}); store this token now:")
        # The token alone on stdout, so `TOKEN=$(manage.py create_api_client x)` works.
        self.stdout.write(token)
