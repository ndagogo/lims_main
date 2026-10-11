import uuid
from django.db import models, transaction
from django.conf import settings
from core.models import TimeStampedModel, Centre, SequenceCounter
TITLE_CHOICES=[('','—'),('Mr','Mr'),('Mrs','Mrs'),('Miss','Miss'),('Ms','Ms'),('Dr','Dr'),('Chief','Chief'),('Alhaji','Alhaji'),('Alhaja','Alhaja'),('Prof','Prof'),('Master','Master')]
SEX_CHOICES=[('','Not specified'),('Female','Female'),('Male','Male'),('Other','Other')]
RACE_CHOICES=[('','Not specified'),('BLACK','Black / African'),('WHITE','White'),('ASIAN','Asian'),('MIXED','Mixed'),('OTHER','Other')]
STATE_CHOICES=[('','—')]+[(name,name) for name in ['Abia','Adamawa','Akwa Ibom','Anambra','Bauchi','Bayelsa','Benue','Borno','Cross River','Delta','Ebonyi','Edo','Ekiti','Enugu','FCT','Gombe','Imo','Jigawa','Kaduna','Kano','Katsina','Kebbi','Kogi','Kwara','Lagos','Nasarawa','Niger','Ogun','Ondo','Osun','Oyo','Plateau','Rivers','Sokoto','Taraba','Yobe','Zamfara']]

class Patient(TimeStampedModel):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    uhid=models.CharField(max_length=32,unique=True,editable=False)
    title=models.CharField(max_length=20,blank=True,default='',choices=TITLE_CHOICES)
    first_name=models.CharField(max_length=100)
    last_name=models.CharField(max_length=100)
    other_names=models.CharField(max_length=100,blank=True)
    phone_country_code=models.CharField(max_length=8,default='+234')
    phone_number=models.CharField(max_length=30,db_index=True)
    alt_phone=models.CharField(max_length=30,blank=True,default='')
    email=models.EmailField(blank=True)
    notify_sms=models.BooleanField(default=True, help_text='Patient consents to SMS notifications.')
    notify_email=models.BooleanField(default=False, help_text='Patient consents to email notifications.')
    notify_whatsapp=models.BooleanField(default=False, help_text='Patient consents to WhatsApp notifications.')
    date_of_birth=models.DateField(null=True,blank=True)
    sex=models.CharField(max_length=20,blank=True,choices=SEX_CHOICES)
    race=models.CharField(max_length=20,blank=True,default='',choices=RACE_CHOICES)
    address=models.TextField(blank=True)
    city=models.CharField(max_length=80,blank=True,default='')
    state=models.CharField(max_length=40,blank=True,default='',choices=STATE_CHOICES)
    health_card_number=models.CharField(max_length=40,blank=True,default='')
    passport_number=models.CharField(max_length=40,blank=True,default='')
    def save(self,*args,**kwargs):
        if not self.uhid:
            with transaction.atomic():
                counter,_=SequenceCounter.objects.select_for_update().get_or_create(key='patient-uhid')
                counter.value+=1; counter.save(update_fields=['value'])
                self.uhid=f'HFDN{counter.value:09d}'
        super().save(*args,**kwargs)
    def __str__(self): return f'{self.uhid} — {self.first_name} {self.last_name}'
HOW_HEARD_CHOICES=[('','—'),('WALKIN','Walk-in'),('FAMILY','Family or friend'),('DOCTOR','Referring doctor'),('HOSPITAL','Hospital'),('SOCIAL','Social media'),('CORPORATE','Employer or corporate'),('OTHER','Other')]
CHANNEL_CHOICES=[('SELF','Self / walk-in'),('DOCTOR','Referral doctor'),('CORPORATE','Corporate client'),('HMO','HMO')]

class OrganisationClient(TimeStampedModel):
    """An HMO or corporate account the front desk can attach to a visit. Administrators maintain the list."""
    KINDS = [('CORPORATE', 'Corporate client'), ('HMO', 'HMO')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    centre = models.ForeignKey(Centre, on_delete=models.PROTECT, related_name='organisation_clients')
    kind = models.CharField(max_length=12, choices=KINDS)
    name = models.CharField(max_length=180)
    code = models.CharField(max_length=40, blank=True, default='')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        constraints = [models.UniqueConstraint(fields=['centre', 'kind', 'name'], name='uniq_organisation_client_name')]

    def __str__(self):
        return f'{self.get_kind_display()} — {self.name}'

class Visit(TimeStampedModel):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    visit_id=models.CharField(max_length=20,unique=True,editable=False)
    patient=models.ForeignKey(Patient,on_delete=models.PROTECT,related_name='visits')
    centre=models.ForeignKey(Centre,on_delete=models.PROTECT)
    registered_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL)
    referral_source=models.CharField(max_length=30,choices=CHANNEL_CHOICES,default='SELF')
    clinical_history=models.TextField(blank=True,default='')
    comments=models.TextField(blank=True,default='')
    trf_number=models.CharField(max_length=40,blank=True,default='')
    reference_1=models.CharField(max_length=40,blank=True,default='')
    reference_2=models.CharField(max_length=40,blank=True,default='')
    client=models.ForeignKey(OrganisationClient,null=True,blank=True,on_delete=models.PROTECT,related_name='visits')
    client_name=models.CharField(max_length=180,blank=True,default='')
    client_employee_number=models.CharField(max_length=40,blank=True,default='')
    how_heard=models.CharField(max_length=20,blank=True,default='',choices=HOW_HEARD_CHOICES)
    status=models.CharField(max_length=20,default='REGISTERED')
    def save(self,*args,**kwargs):
        if not self.visit_id:
            with transaction.atomic():
                key=f'visit-{self.centre.code}'
                counter,_=SequenceCounter.objects.select_for_update().get_or_create(key=key)
                counter.value+=1; counter.save(update_fields=['value'])
                self.visit_id=f'{self.centre.code}{counter.value:07d}'
        super().save(*args,**kwargs)
    def __str__(self): return self.visit_id
class PatientDocument(TimeStampedModel):
    visit=models.ForeignKey(Visit,on_delete=models.CASCADE,related_name='documents')
    document_type=models.CharField(max_length=30,choices=[('REFERRAL','Referral form'),('REGISTRATION','Registration form'),('OTHER','Other')])
    file=models.FileField(upload_to='patient-documents/%Y/%m/')
    uploaded_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL)
    class Meta: ordering=['-created_at']
