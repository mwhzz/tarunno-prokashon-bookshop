from django.db import migrations


def backfill(apps, schema_editor):
    """আগের ফেরতের নিয়মে বিল থেকে বইয়ের পুরো দাম (দাম × কপি) কাটা হতো — সেটাই পুরনো রেকর্ডের অঙ্ক হিসেবে বসে।
    যে মেমো থেকে বইটা পরে সরে গেছে, সেখানে দাম জানা নেই বলে ০ থাকে।"""
    SaleReturn = apps.get_model('sales', 'SaleReturn')
    SaleItem = apps.get_model('sales', 'SaleItem')
    for ret in SaleReturn.objects.using(schema_editor.connection.alias).filter(amount=0):
        item = SaleItem.objects.using(schema_editor.connection.alias).filter(sale_id=ret.sale_id, book_id=ret.book_id).first()
        if item:
            ret.amount = item.unit_price * ret.quantity
            ret.save(update_fields=['amount'])


class Migration(migrations.Migration):
    dependencies = [
        ('sales', '0022_externaltrade_purchase_account_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
