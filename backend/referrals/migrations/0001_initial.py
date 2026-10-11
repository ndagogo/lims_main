import uuid
from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ('core', '0001_initial'),
        ('patients', '0001_initial'),
        ('billing', '0002_phase2_accounts'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='ReferralDoctor',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('doctor_code', models.CharField(max_length=40, unique=True)),
                ('title', models.CharField(blank=True, max_length=30)),
                ('first_name', models.CharField(max_length=100)),
                ('last_name', models.CharField(max_length=100)),
                ('facility_name', models.CharField(blank=True, max_length=180)),
                ('phone', models.CharField(blank=True, max_length=30)),
                ('email', models.EmailField(blank=True, max_length=254)),
                ('commission_type', models.CharField(choices=[('PERCENT', 'Percentage'), ('FIXED', 'Fixed per eligible invoice')], default='PERCENT', max_length=10)),
                ('commission_value', models.DecimalField(decimal_places=2, max_digits=10, validators=[MinValueValidator(Decimal('0'))])),
                ('is_active', models.BooleanField(default=True)),
                ('notes', models.TextField(blank=True)),
                ('centre', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='referral_doctors', to='core.centre')),
            ],
            options={'ordering': ['last_name', 'first_name']},
        ),
        migrations.CreateModel(
            name='ReferralAttribution',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('note', models.CharField(blank=True, max_length=255)),
                ('doctor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='attributions', to='referrals.referraldoctor')),
                ('recorded_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('visit', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='referral_attribution', to='patients.visit')),
            ],
        ),
        migrations.CreateModel(
            name='CommissionLedgerEntry',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('entry_type', models.CharField(choices=[('ACCRUAL', 'Accrual'), ('REVERSAL', 'Reversal'), ('ADJUSTMENT', 'Adjustment'), ('SETTLEMENT', 'Settlement')], max_length=12)),
                ('amount', models.DecimalField(decimal_places=2, help_text='Positive accrual, negative reversal/settlement.', max_digits=12)),
                ('currency', models.CharField(default='NGN', max_length=3)),
                ('description', models.CharField(max_length=255)),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('doctor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='ledger_entries', to='referrals.referraldoctor')),
                ('invoice', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='commission_entries', to='billing.invoice')),
                ('source_entry', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='reversals', to='referrals.commissionledgerentry')),
            ],
            options={'ordering': ['created_at'], 'permissions': [('approve_referral_settlement', 'Can approve referral settlement')]},
        ),
        migrations.CreateModel(
            name='WithdrawalRequest',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12, validators=[MinValueValidator(Decimal('0.01'))])),
                ('requested_at', models.DateTimeField(auto_now_add=True)),
                ('status', models.CharField(choices=[('REQUESTED', 'Requested'), ('APPROVED', 'Approved'), ('REJECTED', 'Rejected'), ('SETTLED', 'Settled')], default='REQUESTED', max_length=12)),
                ('proof_of_payment', models.FileField(blank=True, null=True, upload_to='referral-settlements/%Y/%m/')),
                ('review_note', models.TextField(blank=True)),
                ('settled_at', models.DateTimeField(blank=True, null=True)),
                ('doctor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='withdrawals', to='referrals.referraldoctor')),
                ('requested_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='referral_withdrawals_requested', to=settings.AUTH_USER_MODEL)),
                ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='referral_withdrawals_reviewed', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-requested_at']},
        ),
    ]
