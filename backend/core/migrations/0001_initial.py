import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.contrib.auth.models
import django.contrib.auth.validators
import django.utils.timezone

class Migration(migrations.Migration):
    initial = True
    dependencies = [('auth','0012_alter_user_first_name_max_length')]
    operations = [
        migrations.CreateModel(name='User', fields=[
            ('password', models.CharField(max_length=128, verbose_name='password')),
            ('last_login', models.DateTimeField(blank=True, null=True, verbose_name='last login')),
            ('is_superuser', models.BooleanField(default=False, help_text='Designates that this user has all permissions without explicitly assigning them.', verbose_name='superuser status')),
            ('username', models.CharField(error_messages={'unique':'A user with that username already exists.'}, help_text='Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.', max_length=150, unique=True, validators=[django.contrib.auth.validators.UnicodeUsernameValidator()], verbose_name='username')),
            ('first_name', models.CharField(blank=True, max_length=150, verbose_name='first name')),
            ('last_name', models.CharField(blank=True, max_length=150, verbose_name='last name')),
            ('email', models.EmailField(max_length=254, unique=True)),
            ('is_staff', models.BooleanField(default=False, help_text='Designates whether the user can log into this admin site.', verbose_name='staff status')),
            ('is_active', models.BooleanField(default=True, help_text='Designates whether this user should be treated as active. Unselect this instead of deleting accounts.', verbose_name='active')),
            ('date_joined', models.DateTimeField(default=django.utils.timezone.now, verbose_name='date joined')),
            ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ('display_name', models.CharField(blank=True, max_length=180)),
            ('must_change_password', models.BooleanField(default=False)),
            ('failed_login_count', models.PositiveSmallIntegerField(default=0)),
            ('last_password_change', models.DateTimeField(blank=True, null=True)),
            ('groups', models.ManyToManyField(blank=True, help_text='The groups this user belongs to. A user will get all permissions granted to each of their groups.', related_name='user_set', related_query_name='user', to='auth.group', verbose_name='groups')),
            ('user_permissions', models.ManyToManyField(blank=True, help_text='Specific permissions for this user.', related_name='user_set', related_query_name='user', to='auth.permission', verbose_name='user permissions')),
        ], options={'verbose_name':'user','verbose_name_plural':'users','abstract':False}, managers=[('objects', django.contrib.auth.models.UserManager())]),
        migrations.CreateModel(name='Centre', fields=[('created_at',models.DateTimeField(auto_now_add=True)),('updated_at',models.DateTimeField(auto_now=True)),('id',models.UUIDField(default=uuid.uuid4,editable=False,primary_key=True,serialize=False)),('code',models.CharField(max_length=8,unique=True)),('name',models.CharField(max_length=120)),('address',models.TextField(blank=True)),('phone',models.CharField(blank=True,max_length=30)),('email',models.EmailField(blank=True,max_length=254)),('timezone',models.CharField(default='Africa/Lagos',max_length=64)),('is_active',models.BooleanField(default=True))], options={'ordering':['name'],'permissions':[('manage_centre_users','Can manage centre user access')]}),
        migrations.CreateModel(name='SequenceCounter', fields=[('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),('key',models.CharField(max_length=100,unique=True)),('value',models.PositiveBigIntegerField(default=0))]),
        migrations.CreateModel(name='AuditEvent', fields=[('created_at',models.DateTimeField(auto_now_add=True)),('updated_at',models.DateTimeField(auto_now=True)),('id',models.UUIDField(default=uuid.uuid4,editable=False,primary_key=True,serialize=False)),('action',models.CharField(db_index=True,max_length=80)),('object_type',models.CharField(db_index=True,max_length=100)),('object_id',models.CharField(blank=True,max_length=100)),('path',models.CharField(blank=True,max_length=500)),('method',models.CharField(blank=True,max_length=10)),('ip_address',models.GenericIPAddressField(blank=True,null=True)),('browser',models.CharField(blank=True,max_length=255)),('detail',models.JSONField(blank=True,default=dict)),('actor',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='audit_events',to=settings.AUTH_USER_MODEL)),('centre',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,to='core.centre'))], options={'ordering':['-created_at'],'permissions':[('view_sensitive_audit_details','Can view sensitive audit details')]}),
        migrations.CreateModel(name='UserCentreAccess', fields=[('created_at',models.DateTimeField(auto_now_add=True)),('updated_at',models.DateTimeField(auto_now=True)),('id',models.UUIDField(default=uuid.uuid4,editable=False,primary_key=True,serialize=False)),('can_view',models.BooleanField(default=True)),('can_register',models.BooleanField(default=False)),('can_edit',models.BooleanField(default=False)),('can_approve',models.BooleanField(default=False)),('is_default',models.BooleanField(default=False)),('centre',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='user_access',to='core.centre')),('granted_by',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='centre_access_grants',to=settings.AUTH_USER_MODEL)),('user',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='centre_access',to=settings.AUTH_USER_MODEL))], options={'ordering':['centre__name']}),
        migrations.AddConstraint(model_name='usercentreaccess', constraint=models.UniqueConstraint(fields=('user','centre'),name='unique_user_centre_access')),
    ]
