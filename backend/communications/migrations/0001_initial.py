import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ('core', '0001_initial'),
        ('patients', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='NotificationOutbox',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('channel', models.CharField(choices=[('SMS', 'SMS'), ('EMAIL', 'Email'), ('WHATSAPP', 'WhatsApp')], max_length=10)),
                ('recipient', models.CharField(max_length=255)),
                ('subject', models.CharField(blank=True, max_length=200)),
                ('body', models.TextField()),
                ('status', models.CharField(choices=[('QUEUED', 'Queued'), ('SENDING', 'Sending'), ('SENT', 'Sent'), ('FAILED', 'Failed'), ('CANCELLED', 'Cancelled')], db_index=True, default='QUEUED', max_length=12)),
                ('provider_message_id', models.CharField(blank=True, max_length=200)),
                ('attempts', models.PositiveSmallIntegerField(default=0)),
                ('last_error', models.TextField(blank=True)),
                ('scheduled_for', models.DateTimeField(blank=True, null=True)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('centre', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='notifications', to='core.centre')),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('visit', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='notifications', to='patients.visit')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='NotificationTemplate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('key', models.SlugField(max_length=80, unique=True)),
                ('name', models.CharField(max_length=120)),
                ('channel', models.CharField(choices=[('SMS', 'SMS'), ('EMAIL', 'Email'), ('WHATSAPP', 'WhatsApp')], max_length=10)),
                ('subject_template', models.CharField(blank=True, max_length=200)),
                ('body_template', models.TextField()),
                ('is_active', models.BooleanField(default=True)),
            ],
        ),
    ]
