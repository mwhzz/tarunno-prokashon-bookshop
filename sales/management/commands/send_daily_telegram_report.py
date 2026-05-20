import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from sales.daily_report import (
    build_daily_owner_summary,
    get_latest_telegram_chat,
    send_telegram_owner_report,
)


class Command(BaseCommand):
    help = "Send the daily sales/accounts summary to the owner over Telegram."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            help="Report date in YYYY-MM-DD format. Defaults to today's local date.",
        )
        parser.add_argument(
            "--yesterday",
            action="store_true",
            help="Send yesterday's report instead of today's report.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the message without sending it to Telegram.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Send even when TELEGRAM_REPORT_ENABLED is false.",
        )
        parser.add_argument(
            "--get-chat-id",
            action="store_true",
            help="Print the latest Telegram chat id seen by the bot.",
        )

    def handle(self, *args, **options):
        if options["get_chat_id"]:
            try:
                chat = get_latest_telegram_chat()
            except Exception as exc:
                raise CommandError(str(exc)) from exc

            if not chat:
                raise CommandError("No Telegram updates found. Send /start to the bot first.")

            self.stdout.write(f"Chat ID: {chat['id']}")
            self.stdout.write(f"Chat: {chat['name']} ({chat['type']})")
            return

        if options["date"] and options["yesterday"]:
            raise CommandError("Use either --date or --yesterday, not both.")

        if options["date"]:
            try:
                report_date = datetime.date.fromisoformat(options["date"])
            except ValueError as exc:
                raise CommandError("Invalid --date. Use YYYY-MM-DD.") from exc
        elif options["yesterday"]:
            report_date = timezone.localdate() - datetime.timedelta(days=1)
        else:
            report_date = timezone.localdate()

        message = build_daily_owner_summary(report_date)

        if options["dry_run"]:
            self.stdout.write(message)
            return

        if not settings.TELEGRAM_REPORT_ENABLED and not options["force"]:
            raise CommandError(
                "TELEGRAM_REPORT_ENABLED is false. Set it to True or pass --force."
            )

        try:
            response = send_telegram_owner_report(message)
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        result = response.get("result") if isinstance(response, dict) else None
        message_id = result.get("message_id") if result else ""
        suffix = f" Message ID: {message_id}" if message_id else ""
        self.stdout.write(self.style.SUCCESS(f"Daily Telegram report sent.{suffix}"))
