from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from .models import Patient, Visit, PatientDocument
from .registration import normalize_phone_digits, validate_local_phone, validate_optional_email
from core.models import UserCentreAccess

class PatientSerializer(serializers.ModelSerializer):
    class Meta:
        model=Patient
        fields=['id','uhid','title','first_name','last_name','other_names','phone_country_code','phone_number','alt_phone','email','notify_sms','notify_email','notify_whatsapp','date_of_birth','sex','race','address','city','state','health_card_number','passport_number','created_at']
        read_only_fields=['id','uhid','created_at']
    def validate_phone_number(self, value):
        try:
            return validate_local_phone(value, field_label='Mobile')
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
    def validate_alt_phone(self, value):
        if not (value or '').strip():
            return ''
        try:
            return validate_local_phone(value, field_label='Alternate mobile')
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
    def validate_email(self, value):
        try:
            return validate_optional_email(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
    def validate(self, attrs):
        first=(attrs.get('first_name') or getattr(self.instance,'first_name','')).strip()
        last=(attrs.get('last_name') or getattr(self.instance,'last_name','')).strip()
        phone=normalize_phone_digits(attrs.get('phone_number') or getattr(self.instance,'phone_number',''))
        dob=attrs.get('date_of_birth',getattr(self.instance,'date_of_birth',None))
        qs=Patient.objects.filter(first_name__iexact=first,last_name__iexact=last,phone_number=phone)
        if dob: qs=qs.filter(date_of_birth=dob)
        if self.instance: qs=qs.exclude(pk=self.instance.pk)
        if qs.exists(): raise serializers.ValidationError({'duplicate':'A patient with the same name and phone number (and date of birth where supplied) already exists. Verify the existing UHID before creating another record.'})
        return attrs

class PatientDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model=PatientDocument
        fields=['id','visit','document_type','file','uploaded_by','created_at']
        read_only_fields=['id','uploaded_by','created_at']
    def validate_file(self, file):
        from django.core.exceptions import ValidationError
        from core.uploads import validate_upload
        try:
            validate_upload(file)
        except ValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        return file

class VisitSerializer(serializers.ModelSerializer):
    patient_uhid=serializers.CharField(source='patient.uhid',read_only=True)
    centre_name=serializers.CharField(source='centre.name',read_only=True)
    documents=PatientDocumentSerializer(many=True,read_only=True)
    class Meta:
        model=Visit
        fields=['id','visit_id','patient','patient_uhid','centre','centre_name','registered_by','referral_source','clinical_history','comments','trf_number','reference_1','reference_2','client_name','client_employee_number','how_heard','status','documents','created_at']
        read_only_fields=['id','visit_id','registered_by','created_at']
    def validate(self, attrs):
        centre=attrs.get('centre')
        request=self.context.get('request')
        if centre and request and not request.user.is_superuser and not UserCentreAccess.objects.filter(user=request.user,centre=centre,can_register=True,centre__is_active=True).exists():
            raise serializers.ValidationError({'centre':'You do not have registration access at this centre.'})
        return attrs
