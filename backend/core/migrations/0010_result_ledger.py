import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0009_user_staff_demographics'),
        ('patients', '0007_patient_guardian'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ResultLedger',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('service', models.CharField(choices=[('RADIOLOGY', 'Radiology'), ('LABORATORY', 'Laboratory')], db_index=True, max_length=20)),
                ('kind', models.CharField(choices=[('DISPATCH', 'Dispatch'), ('MODIFY', 'Return for modification'), ('APPROVE', 'Approve')], db_index=True, max_length=20)),
                ('object_type', models.CharField(max_length=40)),
                ('object_id', models.CharField(db_index=True, max_length=64)),
                ('summary', models.CharField(blank=True, max_length=200)),
                ('reason', models.TextField(blank=True)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='result_ledger_entries', to=settings.AUTH_USER_MODEL)),
                ('visit', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='result_ledger', to='patients.visit')),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['service', 'kind', '-created_at'], name='core_result_svc_kind_idx')],
            },
        ),
    ]
