import json
import re
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Sum
from django.utils import timezone

from accounts.models import CashTransaction, DailyCash
from sales.models import Payment
from sales.reports import sales_report


IN_TRANSACTION_TYPES = ("sale", "cash_in", "adjustment_in")
OUT_TRANSACTION_TYPES = ("expense", "cash_out", "adjustment_out")
REPORT_KEYWORDS = ("report", "hisab", "hishab", "hiseb", "হিসাব", "রিপোর্ট")
TELEGRAM_REPORT_KEYBOARD = {
    "keyboard": [
        [{"text": "Today's Report"}, {"text": "Yesterday's Report"}],
        [{"text": "Help"}, {"text": "Date Format"}],
    ],
    "resize_keyboard": True,
    "one_time_keyboard": False,
    "input_field_placeholder": "Choose a report",
}


def money(value):
    amount = Decimal(str(value or 0))
    return f"BDT {amount:,.2f}"


def _sum(queryset, field):
    return queryset.aggregate(total=Sum(field))["total"] or Decimal("0")


def _payment_method_summary(report_date):
    payments = Payment.objects.filter(created_at__date=report_date)
    by_method = payments.values("method").annotate(total=Sum("amount")).order_by("method")
    return {
        "total": _sum(payments, "amount"),
        "by_method": {item["method"]: item["total"] or Decimal("0") for item in by_method},
    }


def _daily_owner_summary_data(report_date=None):
    report_date = report_date or timezone.localdate()
    report = sales_report(report_date, report_date)
    return {
        "report_date": report_date,
        "report": report,
        "summary": report["summary"],
        "payment_summary": _payment_method_summary(report_date),
        "cash": _cash_summary(report_date),
    }


def _cash_summary(report_date):
    daily_cash = DailyCash.objects.filter(date=report_date).first()
    if not daily_cash:
        return {
            "opening_balance": Decimal("0"),
            "closing_balance": Decimal("0"),
            "cash_in": Decimal("0"),
            "cash_out": Decimal("0"),
            "is_closed": False,
            "has_record": False,
        }

    daily_cash.update_closing_balance()
    transactions = CashTransaction.objects.filter(daily_cash=daily_cash)
    return {
        "opening_balance": daily_cash.opening_balance,
        "closing_balance": daily_cash.closing_balance,
        "cash_in": _sum(transactions.filter(transaction_type__in=IN_TRANSACTION_TYPES), "amount"),
        "cash_out": _sum(transactions.filter(transaction_type__in=OUT_TRANSACTION_TYPES), "amount"),
        "is_closed": daily_cash.is_closed,
        "has_record": True,
    }


def build_daily_owner_summary(report_date=None):
    data = _daily_owner_summary_data(report_date)
    report_date = data["report_date"]
    report = data["report"]
    summary = data["summary"]
    payment_summary = data["payment_summary"]
    cash = data["cash"]

    lines = [
        "Tarunno Prokashon - Daily Summary",
        f"Date: {report_date:%d %b %Y}",
        "",
        f"Invoices: {summary['total_invoices']}",
        f"Sales: {money(summary['total_revenue'])}",
        f"Invoice Paid: {money(summary['total_paid'])}",
        f"New Due: {money(summary['total_due'])}",
        f"Collections: {money(payment_summary['total'])}",
        f"Expenses: {money(summary['total_expenses'])}",
        "",
        f"Gross Profit: {money(summary['gross_profit'])}",
        f"Realized Profit: {money(summary['realized_profit'])}",
        f"Net Profit: {money(summary['net_profit'])}",
        "",
        f"Cash Opening: {money(cash['opening_balance'])}",
        f"Cash In: {money(cash['cash_in'])}",
        f"Cash Out: {money(cash['cash_out'])}",
        f"Cash Closing: {money(cash['closing_balance'])}",
        f"Cash Closed: {'Yes' if cash['is_closed'] else 'No'}",
        "",
        f"Total Receivable: {money(summary['total_receivable'])}",
    ]

    method_labels = {
        "cash": "Cash",
        "bank": "Bank",
        "mobile": "Mobile",
        "credit": "Credit",
    }
    if payment_summary["by_method"]:
        lines.extend(["", "Collections by method:"])
        for method, amount in payment_summary["by_method"].items():
            lines.append(f"- {method_labels.get(method, method.title())}: {money(amount)}")

    top_books = report.get("top_books") or []
    if top_books:
        lines.extend(["", "Top books:"])
        for index, book in enumerate(top_books[:3], start=1):
            title = book.get("book__title") or "Untitled"
            qty = book.get("total_qty") or 0
            revenue = money(book.get("total_revenue") or 0)
            lines.append(f"{index}. {title} - {qty} pcs, {revenue}")

    due_customers = report.get("top_due_customers") or []
    if due_customers:
        lines.extend(["", "Top due customers:"])
        for customer in due_customers[:3]:
            lines.append(f"- {customer['name']}: {money(customer['due'])}")

    if not cash["has_record"]:
        lines.extend(["", "Note: No cash record was found for this date."])

    return "\n".join(lines)


def build_telegram_daily_owner_summary(report_date=None):
    data = _daily_owner_summary_data(report_date)
    report_date = data["report_date"]
    report = data["report"]
    summary = data["summary"]
    payment_summary = data["payment_summary"]
    cash = data["cash"]

    lines = [
        "<b>Tarunno Prokashon</b>",
        "<b>Daily Summary</b>",
        f"<i>{report_date:%d %b %Y}</i>",
        "",
        "<b>Sales</b>",
        f"Invoices: <b>{summary['total_invoices']}</b>",
        f"Sales: <code>{money(summary['total_revenue'])}</code>",
        f"Invoice paid: <code>{money(summary['total_paid'])}</code>",
        f"New due: <code>{money(summary['total_due'])}</code>",
        f"Collections: <code>{money(payment_summary['total'])}</code>",
        f"Expenses: <code>{money(summary['total_expenses'])}</code>",
        "",
        "<b>Profit</b>",
        f"Gross profit: <code>{money(summary['gross_profit'])}</code>",
        f"Realized profit: <code>{money(summary['realized_profit'])}</code>",
        f"Net profit: <code>{money(summary['net_profit'])}</code>",
        "",
        "<b>Cash</b>",
        f"Opening: <code>{money(cash['opening_balance'])}</code>",
        f"In: <code>{money(cash['cash_in'])}</code>",
        f"Out: <code>{money(cash['cash_out'])}</code>",
        f"Closing: <code>{money(cash['closing_balance'])}</code>",
        f"Closed: <b>{'Yes' if cash['is_closed'] else 'No'}</b>",
        "",
        "<b>Receivable</b>",
        f"Total receivable: <code>{money(summary['total_receivable'])}</code>",
    ]

    if payment_summary["by_method"]:
        method_labels = {
            "cash": "Cash",
            "bank": "Bank",
            "mobile": "Mobile",
            "credit": "Credit",
        }
        lines.extend(["", "<b>Collections by Method</b>"])
        for method, amount in payment_summary["by_method"].items():
            label = method_labels.get(method, method.title())
            lines.append(f"{escape(label)}: <code>{money(amount)}</code>")

    top_books = report.get("top_books") or []
    if top_books:
        lines.extend(["", "<b>Top Books</b>"])
        for index, book in enumerate(top_books[:3], start=1):
            title = escape(book.get("book__title") or "Untitled")
            qty = book.get("total_qty") or 0
            revenue = money(book.get("total_revenue") or 0)
            lines.append(f"{index}. {title} - {qty} pcs, <code>{revenue}</code>")

    due_customers = report.get("top_due_customers") or []
    if due_customers:
        lines.extend(["", "<b>Top Due Customers</b>"])
        for customer in due_customers[:3]:
            lines.append(f"{escape(customer['name'])}: <code>{money(customer['due'])}</code>")

    if not cash["has_record"]:
        lines.extend(["", "<i>Note: No cash record was found for this date.</i>"])

    return "\n".join(lines)


def _required_setting(name):
    value = getattr(settings, name, "")
    if not value:
        raise ImproperlyConfigured(f"{name} is required for Telegram report sending.")
    return value


def _telegram_request(bot_token, method, payload=None, request_timeout=30):
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    data = None
    headers = {}
    if payload is not None:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if payload else "GET")

    try:
        with urllib.request.urlopen(request, timeout=request_timeout) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram API returned HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not connect to Telegram API: {exc.reason}") from exc

    if not parsed.get("ok"):
        raise RuntimeError(f"Telegram API error: {parsed}")
    return parsed


def _extract_message(update):
    return (
        update.get("message")
        or update.get("channel_post")
        or update.get("edited_message")
        or update.get("edited_channel_post")
    )


def _chat_label(chat):
    return chat.get("title") or " ".join(
        part for part in [chat.get("first_name"), chat.get("last_name")] if part
    )


def get_latest_telegram_chat(bot_token=None):
    bot_token = bot_token or _required_setting("TELEGRAM_BOT_TOKEN")
    data = _telegram_request(bot_token, "getUpdates")

    for update in reversed(data.get("result", [])):
        message = _extract_message(update)
        if not message:
            continue

        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            continue

        name = _chat_label(chat)
        return {"id": str(chat_id), "name": name or str(chat_id), "type": chat.get("type", "")}

    return None


def send_telegram_message(message, chat_id=None, parse_mode=None, reply_markup=None):
    bot_token = _required_setting("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or _required_setting("TELEGRAM_CHAT_ID")
    payload = {
        "chat_id": chat_id,
        "text": message,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)

    return _telegram_request(bot_token, "sendMessage", payload)


def set_telegram_bot_commands():
    bot_token = _required_setting("TELEGRAM_BOT_TOKEN")
    return _telegram_request(
        bot_token,
        "setMyCommands",
        {
            "commands": json.dumps(
                [
                    {"command": "report", "description": "Today's report"},
                    {"command": "yesterday", "description": "Yesterday's report"},
                    {"command": "help", "description": "Show help and buttons"},
                ]
            )
        },
    )


def send_telegram_owner_report(message):
    return send_telegram_message(
        message,
        parse_mode="HTML",
        reply_markup=TELEGRAM_REPORT_KEYBOARD,
    )


def _telegram_state_path():
    configured = getattr(settings, "TELEGRAM_REPORT_STATE_FILE", "")
    if configured:
        return Path(configured)
    return Path(settings.BASE_DIR) / "logs" / "telegram_report_bot_state.json"


def _load_telegram_state():
    path = _telegram_state_path()
    if not path.exists():
        return {}

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_telegram_state(state):
    path = _telegram_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def reset_telegram_report_bot_state():
    path = _telegram_state_path()
    if path.exists():
        path.unlink()
        return True
    return False


def get_telegram_updates_summary(limit=5):
    bot_token = _required_setting("TELEGRAM_BOT_TOKEN")
    data = _telegram_request(bot_token, "getUpdates")
    updates = []
    for update in data.get("result", [])[-limit:]:
        message = _extract_message(update)
        if not message:
            updates.append(
                {
                    "update_id": update.get("update_id"),
                    "chat_id": "",
                    "chat": "",
                    "text": "",
                }
            )
            continue

        chat = message.get("chat") or {}
        updates.append(
            {
                "update_id": update.get("update_id"),
                "chat_id": str(chat.get("id", "")),
                "chat": _chat_label(chat) or str(chat.get("id", "")),
                "text": message.get("text") or "",
            }
        )
    return updates


def _parse_report_date(text):
    normalized = (text or "").strip().lower()
    match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", normalized)
    if match:
        return datetime.strptime(match.group(0), "%Y-%m-%d").date()

    yesterday_words = ("yesterday", "kal", "gotokal", "গতকাল")
    if any(word in normalized for word in yesterday_words):
        return timezone.localdate() - timedelta(days=1)

    return timezone.localdate()


def classify_telegram_report_text(text):
    normalized = (text or "").strip().lower()
    if not normalized:
        return None

    if normalized in ("/start", "start", "/help", "help"):
        return {"type": "help"}

    if normalized in ("date format", "format", "specific date"):
        return {"type": "date_help"}

    if normalized in ("/today", "today", "todays report", "today's report", "today report"):
        return {"type": "report", "date": timezone.localdate()}

    if normalized in (
        "/yesterday",
        "yesterday",
        "yesterdays report",
        "yesterday's report",
        "yesterday report",
    ):
        return {"type": "report", "date": timezone.localdate() - timedelta(days=1)}

    if normalized.startswith("/report") or any(keyword in normalized for keyword in REPORT_KEYWORDS):
        try:
            report_date = _parse_report_date(normalized)
        except ValueError:
            return {"type": "error", "message": "Invalid date. Use: /report YYYY-MM-DD"}
        return {"type": "report", "date": report_date}

    return None


def telegram_report_help_message():
    return "\n".join(
        [
            "<b>Tarunno Prokashon Report Bot</b>",
            "",
            "Use the buttons below, or send one of these:",
            "",
            "<code>/report</code> - today's report",
            "<code>/yesterday</code> - yesterday's report",
            "<code>/report YYYY-MM-DD</code> - a specific date",
        ]
    )


def telegram_date_help_message():
    return "\n".join(
        [
            "<b>Date Format</b>",
            "",
            "Use year-month-day format:",
            "<code>/report 2026-05-20</code>",
            "",
            "You can also use:",
            "<code>/report</code>",
            "<code>/report yesterday</code>",
        ]
    )


def poll_telegram_report_bot(reset_state=False, telegram_timeout=0):
    bot_token = _required_setting("TELEGRAM_BOT_TOKEN")
    allowed_chat_id = str(_required_setting("TELEGRAM_CHAT_ID"))
    if reset_state:
        reset_telegram_report_bot_state()

    state = _load_telegram_state()
    last_update_id = state.get("last_update_id")
    payload = {}
    if last_update_id is not None:
        payload["offset"] = int(last_update_id) + 1
    if telegram_timeout:
        payload["timeout"] = int(telegram_timeout)

    request_timeout = max(30, int(telegram_timeout) + 10) if telegram_timeout else 30
    data = _telegram_request(bot_token, "getUpdates", payload, request_timeout=request_timeout)
    processed = 0
    replied = 0
    ignored_wrong_chat = 0
    ignored_no_command = 0
    last_seen_chat_id = ""

    for update in data.get("result", []):
        update_id = update.get("update_id")
        if update_id is not None:
            last_update_id = max(int(last_update_id or update_id), int(update_id))

        message = _extract_message(update)
        if not message:
            continue

        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is not None:
            last_seen_chat_id = str(chat_id)
        if chat_id is None or str(chat_id) != allowed_chat_id:
            ignored_wrong_chat += 1
            continue

        text = message.get("text") or ""
        request = classify_telegram_report_text(text)
        if not request:
            ignored_no_command += 1
            continue

        processed += 1
        if request["type"] == "help":
            send_telegram_message(
                telegram_report_help_message(),
                chat_id=chat_id,
                parse_mode="HTML",
                reply_markup=TELEGRAM_REPORT_KEYBOARD,
            )
            replied += 1
        elif request["type"] == "date_help":
            send_telegram_message(
                telegram_date_help_message(),
                chat_id=chat_id,
                parse_mode="HTML",
                reply_markup=TELEGRAM_REPORT_KEYBOARD,
            )
            replied += 1
        elif request["type"] == "error":
            send_telegram_message(
                request["message"],
                chat_id=chat_id,
                reply_markup=TELEGRAM_REPORT_KEYBOARD,
            )
            replied += 1
        elif request["type"] == "report":
            send_telegram_message(
                build_telegram_daily_owner_summary(request["date"]),
                chat_id=chat_id,
                parse_mode="HTML",
                reply_markup=TELEGRAM_REPORT_KEYBOARD,
            )
            replied += 1

    if last_update_id is not None:
        state["last_update_id"] = int(last_update_id)
        _save_telegram_state(state)

    return {
        "updates": len(data.get("result", [])),
        "processed": processed,
        "replied": replied,
        "ignored_wrong_chat": ignored_wrong_chat,
        "ignored_no_command": ignored_no_command,
        "last_seen_chat_id": last_seen_chat_id,
        "last_update_id": last_update_id,
    }
