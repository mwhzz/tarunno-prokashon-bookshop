from django.db import migrations


def migrate_external_trades(apps, schema_editor):
    """
    পুরনো ExternalTrade রেকর্ডগুলোকে (এক-লাইন ক্রয়-বিক্রয়) নতুন Vendor/PurchaseBill/
    PurchaseBillItem সিস্টেমে ব্যাকফিল করে — পুরনো CashTransaction রেকর্ড স্পর্শ করা হয় না
    (সেই টাকার হিসাব ইতিমধ্যেই লেজারে সঠিকভাবে রয়েছে), শুধু রিপোর্টিং টেবিলগুলো ভরা হয়।
    legacy_external_trade লিংক থাকায় এই migration দ্বিতীয়বার চালালেও ডুপ্লিকেট হবে না।
    """
    ExternalTrade = apps.get_model('sales', 'ExternalTrade')
    Vendor = apps.get_model('purchase', 'Vendor')
    PurchaseBill = apps.get_model('purchase', 'PurchaseBill')
    PurchaseBillItem = apps.get_model('purchase', 'PurchaseBillItem')

    for trade in ExternalTrade.objects.all():
        if PurchaseBill.objects.filter(legacy_external_trade=trade).exists():
            continue

        vendor_name = trade.publisher.strip() if trade.publisher else 'অজানা ভেন্ডর'
        vendor, _ = Vendor.objects.get_or_create(name=vendor_name)

        note_parts = [f"পুরনো External Trade #{trade.id} থেকে মাইগ্রেট করা হয়েছে।"]
        if trade.status == 'sold':
            note_parts.append(
                f"বিক্রয় তথ্য: {trade.customer_name or 'N/A'} "
                f"({trade.customer_phone or 'N/A'}) — বিক্রয়মূল্য/কপি ৳{trade.selling_price}."
            )
        if trade.note:
            note_parts.append(f"নোট: {trade.note}")

        total = trade.purchase_price * trade.quantity
        bill = PurchaseBill.objects.create(
            vendor=vendor,
            vendor_name=vendor_name,
            subtotal=total,
            discount=0,
            total=total,
            paid_amount=total,
            due_amount=0,
            status='paid',
            purchase_date=trade.created_at.date(),
            note=' '.join(note_parts),
            legacy_external_trade=trade,
        )
        # PurchaseBill.save() is not the model's real save() inside a migration
        # (historical model has no custom methods), so due/status are set explicitly above.

        PurchaseBillItem.objects.create(
            bill=bill,
            book=trade.converted_book,
            book_title=trade.book_title,
            quantity=trade.quantity,
            mrp=trade.body_rate,
            unit_price=trade.purchase_price,
            commission=trade.sale_commission,
            selling_price=trade.selling_price,
            total=total,
        )


def reverse_migration(apps, schema_editor):
    PurchaseBill = apps.get_model('purchase', 'PurchaseBill')
    PurchaseBill.objects.filter(legacy_external_trade__isnull=False).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('purchase', '0001_initial'),
        ('sales', '0014_externaltrade_body_rate_externaltrade_publisher_and_more'),
    ]

    operations = [
        migrations.RunPython(migrate_external_trades, reverse_migration),
    ]
