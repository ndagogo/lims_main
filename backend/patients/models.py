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
    guardian_name=models.CharField(max_length=120, blank=True, default='')
    guardian_relationship=models.CharField(max_length=40, blank=True, default='')
    guardian_phone=models.CharField(max_length=30, blank=True, default='')
    date_of_birth=models.DateField(null=True,blank=True)
    sex=models.CharField(max_length=20,blank=True,choices=SEX_CHOICES)
    race=models.CharField(max_length=20,blank=True,default='',choices=RACE_CHOICES)
    address=models.TextField(blank=True)
    city=models.CharField(max_length=80,blank=True,default='')
    state=models.CharField(max_length=40,blank=True,default='',choices=STATE_CHOICES)
    health_card_number=models.CharField(max_length=40,blank=True,default='')
    passport_number=models.CharField(max_length=40,blank=True,default='')

    class Meta:
        permissions = [
            ('can_modify_patient_data', 'Can modify patient demographic data'),
        ]

    def save(self,*args,**kwargs):
        if not self.uhid:
            with transaction.atomic():
                counter,_=SequenceCounter.objects.select_for_update().get_or_create(key='patient-uhid')
                counter.value+=1; counter.save(update_fields=['value'])
                from core.organization import document_prefix
                self.uhid=f'{document_prefix()}{counter.value:09d}'
        super().save(*args,**kwargs)
    def __str__(self): return f'{self.uhid} — {self.first_name} {self.last_name}'
HOW_HEARD_CHOICES=[('','—'),('WALKIN','Walk-in'),('FAMILY','Family or friend'),('DOCTOR','Referring doctor'),('HOSPITAL','Hospital'),('SOCIAL','Social media'),('CORPORATE','Employer or corporate'),('OTHER','Other')]
CHANNEL_CHOICES = [
    ('SELF', 'Self (B2C)'),
    ('DOCTOR', 'Permanent doctors (B2D)'),
    ('CREDIT_CLIENT', 'Credit clients (B2B)'),
    ('ON_PAYMENT_CLIENT', 'On payment clients (B2B)'),
    # Legacy values kept so historical visits keep a readable label.
    ('CORPORATE', 'Corporate client (legacy)'),
    ('HMO', 'HMO (legacy)'),
]

CLIENT_CATEGORY_CHOICES = [
    ('HMO', 'HMO'),
    ('TPA', 'TPA'),
    ('INSURANCE', 'Insurance'),
    ('CORPORATE', 'Corporate'),
    ('HOSPITAL', 'Hospital'),
    ('CRO', 'CRO'),
    ('PROFICIENCY', 'Proficiency'),
    ('STAFF', 'Staff'),
    ('STAFF_RELATIVE', 'Staff relative'),
    ('LABORATORY', 'Laboratory (pickup point)'),
    ('DIRECT_PATIENT', 'Direct patient'),
    ('COLLECTION_CENTER', 'Collection center'),
    ('LAB_TO_LAB_CREDIT', 'Lab to lab credit'),
    ('LAB_TO_LAB_ON_PAYMENT', 'Lab to lab on payment'),
    ('DISCOUNT', 'Discount client'),
    ('PSU', 'PSU'),
    ('FRANCHISE', 'Franchise'),
    ('OTHER', 'Other'),
]

BILLING_TYPE_CHOICES = [
    ('CREDIT', 'Credit'),
    ('ON_PAYMENT', 'On payment'),
    ('CASH', 'Cash'),
]

INVOICE_FREQUENCY_CHOICES = [
    ('DAILY', 'Daily wise invoice date'),
    ('WEEKLY', 'Weekly wise invoice date'),
    ('MONTHLY', 'Monthly wise invoice date'),
]


class ClientType(TimeStampedModel):
    """Admin-managed client type. Seeded with Credit, On Payment, and Default."""
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=80)
    show_test_prices_default = models.BooleanField(
        default=False,
        help_text='Default Test_Price_Display_on_receipt for new clients of this type. OFF by default.',
    )
    is_system = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


class RegistrationChannel(TimeStampedModel):
    """Per-centre channel configuration: tariff + receipt price display."""
    centre = models.ForeignKey(Centre, on_delete=models.PROTECT, related_name='registration_channels')
    code = models.CharField(max_length=30, choices=[
        ('SELF', 'Self (B2C)'),
        ('DOCTOR', 'Permanent doctors (B2D)'),
        ('CREDIT_CLIENT', 'Credit clients (B2B)'),
        ('ON_PAYMENT_CLIENT', 'On payment clients (B2B)'),
    ])
    name = models.CharField(max_length=80, blank=True, default='')
    tariff = models.ForeignKey(
        'laboratory.Tariff', null=True, blank=True, on_delete=models.PROTECT,
        related_name='registration_channels',
        help_text='Used for Self and Permanent Doctors. B2B clients use their own mapped tariff.',
    )
    show_test_prices = models.BooleanField(
        default=False,
        help_text='Test_Price_Display_on_receipt. OFF by default; turn ON per channel when needed.',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['centre__code', 'code']
        constraints = [
            models.UniqueConstraint(fields=['centre', 'code'], name='uniq_registration_channel_centre_code'),
        ]
        permissions = [
            ('manage_registration_channels', 'Can manage registration channels and tariffs'),
        ]

    def __str__(self):
        return f'{self.centre.code} / {self.get_code_display()}'

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.code in ('SELF', 'DOCTOR') and self.is_active and not self.tariff_id:
            raise ValidationError({'tariff': 'Self and Permanent Doctors channels need a mapped tariff.'})


class OrganisationClient(TimeStampedModel):
    """Client master: HMO / hospital / corporate account with tariff and wallet rules."""
    KINDS = [('CORPORATE', 'Corporate client'), ('HMO', 'HMO')]  # legacy display bridge
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    centre = models.ForeignKey(Centre, on_delete=models.PROTECT, related_name='organisation_clients')
    client_type = models.ForeignKey(
        ClientType, null=True, blank=True, on_delete=models.PROTECT, related_name='clients',
    )
    # Denormalized code for fast filters: CREDIT | ON_PAYMENT | DEFAULT
    client_type_code = models.CharField(max_length=20, blank=True, default='', db_index=True)
    kind = models.CharField(max_length=12, choices=KINDS, blank=True, default='CORPORATE')
    category = models.CharField(max_length=40, choices=CLIENT_CATEGORY_CHOICES, blank=True, default='')
    name = models.CharField(max_length=180)
    code = models.CharField(max_length=40, blank=True, default='')
    tariff = models.ForeignKey(
        'laboratory.Tariff', null=True, blank=True, on_delete=models.PROTECT, related_name='clients',
    )
    show_test_prices = models.BooleanField(
        default=False,
        help_text='Test_Price_Display_on_receipt for this client (subject to channel override). OFF by default.',
    )
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    credit_limit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    credit_wallet_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    credit_wallet_used = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    credit_limit_days = models.PositiveIntegerField(default=0)
    threshold_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    security_deposit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    org_percent = models.DecimalField(max_digits=6, decimal_places=2, default=100)
    emp_percent = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    enable_auto_locking = models.BooleanField(default=False)
    is_copayment = models.BooleanField(default=False)
    billing_type = models.CharField(max_length=20, choices=BILLING_TYPE_CHOICES, blank=True, default='')
    invoice_frequency = models.CharField(max_length=20, choices=INVOICE_FREQUENCY_CHOICES, blank=True, default='MONTHLY')
    address_1 = models.CharField(max_length=180, blank=True, default='')
    address_2 = models.CharField(max_length=180, blank=True, default='')
    area = models.CharField(max_length=80, blank=True, default='')
    city = models.CharField(max_length=80, blank=True, default='')
    state = models.CharField(max_length=80, blank=True, default='')
    country = models.CharField(max_length=80, blank=True, default='Nigeria')
    pin_zip = models.CharField(max_length=20, blank=True, default='')
    phone = models.CharField(max_length=40, blank=True, default='')
    sap_code = models.CharField(max_length=40, blank=True, default='')
    gst_number = models.CharField(max_length=40, blank=True, default='')
    tin_number = models.CharField(max_length=40, blank=True, default='')
    pan_number = models.CharField(max_length=40, blank=True, default='')
    report_dispatch_type = models.CharField(max_length=40, blank=True, default='')
    contact_person = models.CharField(max_length=120, blank=True, default='')
    contact_designation = models.CharField(max_length=80, blank=True, default='')
    contact_phone = models.CharField(max_length=40, blank=True, default='')
    contact_mobile_1 = models.CharField(max_length=40, blank=True, default='')
    contact_mobile_2 = models.CharField(max_length=40, blank=True, default='')
    report_email = models.EmailField(blank=True, default='')
    comm_email = models.EmailField(blank=True, default='')
    nature_of_business = models.CharField(max_length=120, blank=True, default='')
    scope_of_business = models.CharField(max_length=120, blank=True, default='')
    covering_letter_info = models.TextField(blank=True, default='')
    invoice_footer = models.TextField(blank=True, default='')
    portal_username = models.CharField(max_length=80, blank=True, default='')
    agreement_file = models.FileField(upload_to='client-agreements/%Y/%m/', blank=True, null=True)
    email_required = models.BooleanField(default=False)
    sms_required = models.BooleanField(default=False)
    trf_mandatory = models.BooleanField(default=False)
    employee_id_mandatory = models.BooleanField(default=False)
    do_not_process_invoice = models.BooleanField(default=False)
    is_locked = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='clients_created',
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='clients_approved',
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approval_status = models.CharField(
        max_length=20,
        choices=[('DRAFT', 'Draft'), ('PENDING', 'Pending approval'), ('APPROVED', 'Approved'), ('REJECTED', 'Rejected')],
        default='APPROVED',
    )

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                fields=['centre', 'client_type_code', 'name'],
                name='uniq_organisation_client_type_name',
            ),
        ]
        permissions = [
            ('manage_clients', 'Can manage organisation clients'),
            ('topup_credit_wallet', 'Can top up credit client wallets'),
            ('approve_client', 'Can approve organisation clients'),
        ]

    def __str__(self):
        type_label = self.client_type_code or self.kind or 'CLIENT'
        return f'{type_label} — {self.name}'

    def sync_type_fields(self):
        if self.client_type_id:
            self.client_type_code = (self.client_type.code or '').upper()
        if self.client_type_code == 'CREDIT':
            self.kind = 'CORPORATE'
            if not self.billing_type:
                self.billing_type = 'CREDIT'
        elif self.client_type_code == 'ON_PAYMENT':
            self.kind = 'HMO' if self.category == 'HMO' else 'CORPORATE'
            if not self.billing_type:
                self.billing_type = 'ON_PAYMENT'
        elif self.client_type_code == 'DEFAULT':
            if not self.billing_type:
                self.billing_type = 'CASH'

    def save(self, *args, **kwargs):
        self.sync_type_fields()
        if not self.code:
            with transaction.atomic():
                counter, _ = SequenceCounter.objects.select_for_update().get_or_create(key='client-code')
                counter.value += 1
                counter.save(update_fields=['value'])
                self.code = f'CMN{counter.value:04d}'
        super().save(*args, **kwargs)


class CreditWalletLedger(TimeStampedModel):
    """Top-ups, Paid-invoice debits, and restores against a credit client's wallet."""
    ENTRY_TYPES = [('TOP_UP', 'Top-up'), ('DEBIT', 'Debit'), ('ADJUSTMENT', 'Adjustment')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client = models.ForeignKey(OrganisationClient, on_delete=models.PROTECT, related_name='wallet_ledger')
    entry_type = models.CharField(max_length=20, choices=ENTRY_TYPES)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    balance_after = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    reference = models.CharField(max_length=100, blank=True, default='')
    note = models.TextField(blank=True, default='')
    visit = models.ForeignKey('patients.Visit', null=True, blank=True, on_delete=models.SET_NULL, related_name='wallet_entries')
    invoice = models.ForeignKey('billing.Invoice', null=True, blank=True, on_delete=models.SET_NULL, related_name='wallet_entries')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.client.name} {self.entry_type} {self.amount}'


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
    resolved_tariff = models.ForeignKey(
        'laboratory.Tariff', null=True, blank=True, on_delete=models.PROTECT, related_name='visits',
    )
    show_test_prices = models.BooleanField(
        default=False,
        help_text='Snapshot of Test_Price_Display_on_receipt at registration. OFF by default.',
    )
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
