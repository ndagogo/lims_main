from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('radiology', '0009_imaging_order_stat'),
    ]

    operations = [
        migrations.AddField(
            model_name='radiologyexam',
            name='returned_at',
            field=models.DateTimeField(
                blank=True, null=True,
                help_text='Set when the assigned radiologist did not accept before the window ended.',
            ),
        ),
        migrations.CreateModel(
            name='RadiologistAcceptRecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('assigned_at', models.DateTimeField()),
                ('closed_at', models.DateTimeField()),
                ('window_minutes', models.PositiveIntegerField()),
                ('outcome', models.CharField(choices=[('ACCEPTED', 'Accepted'), ('MISSED', 'Not accepted in time')], max_length=12)),
                ('note', models.TextField(blank=True)),
                ('exam', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='accept_records', to='radiology.radiologyexam')),
                ('radiologist', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='radiology_accept_records', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-closed_at']},
        ),
    ]
