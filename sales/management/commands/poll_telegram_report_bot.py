from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

from sales.daily_report import (
    get_telegram_updates_summary,
    poll_telegram_report_bot,
    reset_telegram_report_bot_state,
)


class Command(BaseCommand):
    help = "Poll Telegram updates and reply to owner report commands."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset-state",
            action="store_true",
            help="Forget the last processed Telegram update id before polling.",
        )
        parser.add_argument(
            "--show-updates",
            action="store_true",
            help="Print recent Telegram updates and configured chat id.",
        )

    def handle(self, *args, **options):
        if options["show_updates"]:
            try:
                updates = get_telegram_updates_summary()
            except Exception as exc:
                raise CommandError(str(exc)) from exc

            self.stdout.write(f"Configured chat ID: {settings.TELEGRAM_CHAT_ID or '(empty)'}")
            if not updates:
                self.stdout.write("No pending Telegram updates found. Send /report to the bot, then run this again.")
                return

            for update in updates:
                self.stdout.write(
                    f"Update {update['update_id']}: "
                    f"chat_id={update['chat_id']} "
                    f"chat={update['chat']} "
                    f"text={update['text']}"
                )
            return

        if options["reset_state"]:
            reset_telegram_report_bot_state()

        try:
            result = poll_telegram_report_bot(reset_state=options["reset_state"])
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            "Telegram poll complete. "
            f"Updates: {result['updates']}, "
            f"processed: {result['processed']}, "
            f"replied: {result['replied']}, "
            f"wrong chat: {result['ignored_wrong_chat']}, "
            f"no command: {result['ignored_no_command']}, "
            f"last seen chat: {result['last_seen_chat_id'] or '(none)'}."
        )
