from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0019_organization_idle_timeout'),
    ]

    operations = [
        migrations.AddField(
            model_name='centrereportformat',
            name='header_mode',
            field=models.CharField(
                choices=[('LETTERHEAD', 'Centre letter heading'), ('COMPOSED', 'Logo, address, and phone')],
                default='COMPOSED', max_length=12,
            ),
        ),
        migrations.AddField(
            model_name='centrereportformat',
            name='logo_align',
            field=models.CharField(
                choices=[('LEFT', 'Left'), ('CENTER', 'Centre'), ('RIGHT', 'Right')],
                default='LEFT', max_length=8,
            ),
        ),
        migrations.AddField(
            model_name='centrereportformat',
            name='address_align',
            field=models.CharField(
                choices=[('LEFT', 'Left'), ('CENTER', 'Centre'), ('RIGHT', 'Right')],
                default='CENTER', max_length=8,
            ),
        ),
        migrations.AddField(
            model_name='centrereportformat',
            name='contact_align',
            field=models.CharField(
                choices=[('LEFT', 'Left'), ('CENTER', 'Centre'), ('RIGHT', 'Right')],
                default='CENTER', max_length=8,
            ),
        ),
        migrations.AddField(
            model_name='resultledger',
            name='print_style',
            field=models.CharField(
                blank=True, default='', max_length=20,
                help_text='letterhead or plain. Plain omits the centre header and footer for pre-printed paper.',
            ),
        ),
    ]
