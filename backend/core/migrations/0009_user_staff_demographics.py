from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_audit_trail_enterprise'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='title',
            field=models.CharField(
                blank=True, default='', max_length=20,
                choices=[
                    ('', '—'), ('Mr', 'Mr'), ('Mrs', 'Mrs'), ('Miss', 'Miss'), ('Ms', 'Ms'),
                    ('Dr', 'Dr'), ('Prof', 'Prof'),
                ],
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='other_names',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='user',
            name='date_of_birth',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='user',
            name='sex',
            field=models.CharField(
                blank=True, max_length=20,
                choices=[('', 'Not specified'), ('Female', 'Female'), ('Male', 'Male'), ('Other', 'Other')],
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='marital_status',
            field=models.CharField(
                blank=True, max_length=20,
                choices=[
                    ('', 'Not specified'), ('SINGLE', 'Single'), ('MARRIED', 'Married'),
                    ('DIVORCED', 'Divorced'), ('WIDOWED', 'Widowed'), ('SEPARATED', 'Separated'),
                ],
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='address',
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
