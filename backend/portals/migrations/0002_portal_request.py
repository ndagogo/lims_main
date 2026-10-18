import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('patients', '0006_patient_can_modify_permission'),
        ('portals', '0001_roadmap_gap_remediation'),
    ]

    operations = [
        migrations.CreateModel(
            name='PortalRequest',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('kind', models.CharField(choices=[('SUPPORT', 'Support'), ('CORRECTION', 'Correction')], max_length=12)),
                ('message', models.TextField()),
                ('status', models.CharField(default='OPEN', max_length=12)),
                ('patient', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='portal_requests', to='patients.patient')),
                ('visit', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='portal_requests', to='patients.visit')),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
