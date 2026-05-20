import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from sales.daily_report import poll_telegram_report_bot


class Command(BaseCommand):
    help = "Run the Telegram report bot as a long-polling worker."

    def add_arguments(self, parser):
        parser.add_argument(
            "--timeout",
            type=int,
            default=25,
            help="Telegram long-poll timeout in seconds.",
        )
        parser.add_argument(
            "--error-sleep",
            type=int,
            default=5,
            help="Seconds to wait before retrying after an error.",
        )

    def handle(self, *args, **options):
        timeout = max(1, options["timeout"])
        error_sleep = max(1, options["error_sleep"])
        self.stdout.write(self.style.SUCCESS("Telegram report bot worker started."))

        while True:
            try:
                close_old_connections()
                result = poll_telegram_report_bot(telegram_timeout=timeout)
                if result["processed"] or result["replied"]:
                    self.stdout.write(
                        "Telegram poll: "
                        f"processed={result['processed']} "
                        f"replied={result['replied']} "
                        f"last_update_id={result['last_update_id']}"
                    )
            except KeyboardInterrupt:
                self.stdout.write("Telegram report bot worker stopped.")
                return
            except Exception as exc:
                self.stderr.write(f"Telegram report bot error: {exc}")
                time.sleep(error_sleep)
