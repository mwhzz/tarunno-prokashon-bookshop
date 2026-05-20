import json
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Sum
from django.utils import timezone

from accounts.models import CashTransaction, DailyCash
from sales.models import Payment
from sales.reports import sales_report


IN_TRANSACTION_TYPES = ("sale", "cash_in", "adjustment_in")
OUT_TRANSACTION_TYPES = ("expense", "cash_out", "adjustment_out")


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
    report_date = report_date or timezone.localdate()
    report = sales_report(report_date, report_date)
    summary = report["summary"]
    payment_summary = _payment_method_summary(report_date)
    cash = _cash_summary(report_date)

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


def _required_setting(name):
    value = getattr(settings, name, "")
    if not value:
        raise ImproperlyConfigured(f"{name} is required for Telegram report sending.")
    return value


def _telegram_request(bot_token, method, payload=None):
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    data = None
    headers = {}
    if payload is not None:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if payload else "GET")

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram API returned HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not connect to Telegram API: {exc.reason}") from exc

    if not parsed.get("ok"):
        raise RuntimeError(f"Telegram API error: {parsed}")
    return parsed


def get_latest_telegram_chat(bot_token=None):
    bot_token = bot_token or _required_setting("TELEGRAM_BOT_TOKEN")
    data = _telegram_request(bot_token, "getUpdates")

    for update in reversed(data.get("result", [])):
        message = (
            update.get("message")
            or update.get("channel_post")
            or update.get("edited_message")
            or update.get("edited_channel_post")
        )
        if not message:
            continue

        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            continue

        name = chat.get("title") or " ".join(
            part for part in [chat.get("first_name"), chat.get("last_name")] if part
        )
        return {"id": str(chat_id), "name": name or str(chat_id), "type": chat.get("type", "")}

    return None


def send_telegram_owner_report(message):
    bot_token = _required_setting("TELEGRAM_BOT_TOKEN")
    chat_id = _required_setting("TELEGRAM_CHAT_ID")
    return _telegram_request(
        bot_token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": message,
        },
    )
