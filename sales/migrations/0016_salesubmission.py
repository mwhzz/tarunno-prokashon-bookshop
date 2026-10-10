from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('sales', '0015_payment_discount_alter_payment_method')]
    operations = [
        migrations.CreateModel(
            name='SaleSubmission',
            fields=[
                ('request_id', models.UUIDField(primary_key=True, editable=False, serialize=False)),
                ('payload_hash', models.CharField(max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('sale', models.OneToOneField(null=True, on_delete=django.db.models.deletion.PROTECT, to='sales.sale')),
            ],
        ),
    ]
