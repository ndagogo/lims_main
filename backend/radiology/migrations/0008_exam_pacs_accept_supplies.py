from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('radiology', '0007_modality_worklist_connection'),
    ]

    operations = [
        migrations.AddField(
            model_name='radiologyexam',
            name='accepted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='radiologyexam',
            name='pacs_uploaded_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='radiologyexam',
            name='pacs_uploaded_by',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='radiology_exams_uploaded', to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='radiologyexam',
            name='radiologist_assigned_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='ExamSupplyUse',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('item_name', models.CharField(max_length=80)),
                ('quantity', models.PositiveIntegerField()),
                ('exam', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='supplies', to='radiology.radiologyexam')),
            ],
            options={'ordering': ['item_name']},
        ),
        migrations.CreateModel(
            name='RadiologyDeskSetting',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('accept_minutes', models.PositiveIntegerField(default=30)),
            ],
        ),
    ]
