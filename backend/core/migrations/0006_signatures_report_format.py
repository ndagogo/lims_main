import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0005_access_platform_controls'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='signature',
            field=models.ImageField(
                blank=True,
                help_text='Handwritten or scanned signature shown on authenticated reports.',
                null=True,
                upload_to='signatures/%Y/%m/',
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='signature_title',
            field=models.CharField(
                blank=True,
                help_text='Professional title under the signature, e.g. Consultant Radiologist.',
                max_length=120,
            ),
        ),
        migrations.CreateModel(
            name='CentreReportFormat',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('title_lab', models.CharField(default='Laboratory Report', max_length=120)),
                ('title_radiology', models.CharField(default='Radiology Report', max_length=120)),
                ('header_text', models.TextField(blank=True, help_text='Optional line under the centre name.')),
                ('footer_text', models.TextField(blank=True, default='Released copies are authorised for clinical use.')),
                ('show_logo', models.BooleanField(default=True)),
                ('show_centre_address', models.BooleanField(default=True)),
                ('show_centre_contact', models.BooleanField(default=True)),
                ('primary_color', models.CharField(default='#102033', max_length=7)),
                ('accent_color', models.CharField(default='#0f766e', max_length=7)),
                ('layout_style', models.CharField(
                    choices=[
                        ('CLASSIC', 'Classic (centred letterhead)'),
                        ('COMPACT', 'Compact (dense clinical)'),
                        ('FORMAL', 'Formal (ruled sections)'),
                        ('MODERN', 'Modern (accent bar)'),
                    ],
                    default='CLASSIC',
                    max_length=12,
                )),
                ('font_family', models.CharField(
                    choices=[
                        ('Helvetica', 'Helvetica (sans)'),
                        ('Times-Roman', 'Times (serif)'),
                        ('Courier', 'Courier (mono)'),
                    ],
                    default='Helvetica',
                    max_length=20,
                )),
                ('body_font_size', models.PositiveSmallIntegerField(default=10)),
                ('margin_mm', models.PositiveSmallIntegerField(default=18)),
                ('show_authenticator_signature', models.BooleanField(default=True)),
                ('signature_label', models.CharField(default='Authenticated by', max_length=80)),
                ('watermark_text', models.CharField(blank=True, max_length=80)),
                ('centre', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='report_format',
                    to='core.centre',
                )),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
