import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    initial=True
    dependencies=[('core','0001_initial'),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[
        migrations.CreateModel(name='Patient',fields=[('created_at',models.DateTimeField(auto_now_add=True)),('updated_at',models.DateTimeField(auto_now=True)),('id',models.UUIDField(default=uuid.uuid4,editable=False,primary_key=True,serialize=False)),('uhid',models.CharField(editable=False,max_length=32,unique=True)),('first_name',models.CharField(max_length=100)),('last_name',models.CharField(max_length=100)),('other_names',models.CharField(blank=True,max_length=100)),('phone_number',models.CharField(db_index=True,max_length=30)),('email',models.EmailField(blank=True,max_length=254)),('date_of_birth',models.DateField(blank=True,null=True)),('sex',models.CharField(blank=True,max_length=20)),('address',models.TextField(blank=True))]),
        migrations.CreateModel(name='Visit',fields=[('created_at',models.DateTimeField(auto_now_add=True)),('updated_at',models.DateTimeField(auto_now=True)),('id',models.UUIDField(default=uuid.uuid4,editable=False,primary_key=True,serialize=False)),('visit_id',models.CharField(editable=False,max_length=20,unique=True)),('referral_source',models.CharField(default='SELF',max_length=30)),('status',models.CharField(default='REGISTERED',max_length=20)),('centre',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,to='core.centre')),('patient',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='visits',to='patients.patient')),('registered_by',models.ForeignKey(null=True,on_delete=django.db.models.deletion.SET_NULL,to=settings.AUTH_USER_MODEL))]),
        migrations.CreateModel(name='PatientDocument',fields=[('created_at',models.DateTimeField(auto_now_add=True)),('updated_at',models.DateTimeField(auto_now=True)),('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),('document_type',models.CharField(choices=[('REFERRAL','Referral form'),('REGISTRATION','Registration form'),('OTHER','Other')],max_length=30)),('file',models.FileField(upload_to='patient-documents/%Y/%m/')),('uploaded_by',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,to=settings.AUTH_USER_MODEL)),('visit',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='documents',to='patients.visit'))],options={'ordering':['-created_at']}),
    ]
