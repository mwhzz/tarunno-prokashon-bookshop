from django.core.management.base import BaseCommand, CommandError

from sales.daily_report import poll_telegram_report_bot


class Command(BaseCommand):
    help = "Poll Telegram updates and reply to owner report commands."

    def handle(self, *args, **options):
        try:
            result = poll_telegram_report_bot()
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            "Telegram poll complete. "
            f"Updates: {result['updates']}, "
            f"processed: {result['processed']}, "
            f"replied: {result['replied']}."
        )
