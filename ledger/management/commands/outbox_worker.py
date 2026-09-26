import signal
import time
from types import FrameType
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from ledger.services import outbox


class Command(BaseCommand):
    help = "Publish outbox events until stopped (or once, with --once)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--once", action="store_true", help="Drain the outbox, then exit.")
        parser.add_argument("--batch-size", type=int, default=100)
        parser.add_argument(
            "--interval", type=float, default=1.0, help="Seconds to sleep when idle."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        publisher = outbox.publisher_from_settings()
        batch_size: int = options["batch_size"]
        self._running = True
        previous = {sig: signal.signal(sig, self._stop) for sig in (signal.SIGTERM, signal.SIGINT)}

        total = 0
        try:
            while self._running:
                published = outbox.publish_pending(publisher, batch_size=batch_size)
                total += published
                if published < batch_size:
                    if options["once"]:
                        break
                    time.sleep(options["interval"])
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        self.stdout.write(f"published {total} event(s)")

    def _stop(self, signum: int, frame: FrameType | None) -> None:
        # Finish the batch in hand; its transaction commits before we exit.
        self._running = False
